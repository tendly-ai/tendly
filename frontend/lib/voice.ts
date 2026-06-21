"use client";

// Browser-side Deepgram Voice Agent session.
//
// Runs the conversational agent directly in the renderer: captures the mic as
// linear16 PCM, streams it to Deepgram via @deepgram/sdk, plays the agent's
// audio back with barge-in, and routes the agent's function calls
// (create_care_request / confirm_action) to the FastAPI backend so Claude
// triage stays authoritative.

import { DeepgramClient } from "@deepgram/sdk";
import {
  getAgentConfig,
  createRequest,
  confirmTask,
  type AgentConfig,
} from "./api";
import type { CareRequest } from "./types";

export type AgentState = "connecting" | "listening" | "speaking" | "thinking";

export interface Caption {
  role: "user" | "assistant";
  text: string;
}

export interface VoiceCallbacks {
  onState?: (state: AgentState) => void;
  onCaption?: (caption: Caption) => void;
  onRequest?: (req: CareRequest) => void; // a care request was created
  onConfirmResult?: (status: string, spoken?: string) => void;
  onError?: (message: string) => void;
  onClose?: () => void;
}

const OUTPUT_SAMPLE_RATE = 24000;

function floatTo16BitPCM(input: Float32Array): ArrayBuffer {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out.buffer;
}

export class VoiceAgentSession {
  private readonly patientId: string;
  private readonly cb: VoiceCallbacks;

  private connection: any = null;
  private stream: MediaStream | null = null;
  private micCtx: AudioContext | null = null;
  private processor: ScriptProcessorNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private silentGain: GainNode | null = null;

  private playCtx: AudioContext | null = null;
  private playHead = 0;
  private playSources: AudioBufferSourceNode[] = [];

  private closed = false;
  // request_id of a request awaiting confirmation (for button-driven confirm).
  pendingConfirmId: string | null = null;

  constructor(patientId: string, callbacks: VoiceCallbacks = {}) {
    this.patientId = patientId;
    this.cb = callbacks;
  }

  async start(): Promise<void> {
    this.cb.onState?.("connecting");

    const config: AgentConfig = await getAgentConfig(this.patientId);
    if (!config.enabled || !config.token) {
      throw new Error(config.reason || "Voice agent unavailable");
    }

    // Mic capture — echo cancellation keeps the agent from hearing itself.
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    this.micCtx = new AudioContext();
    const inputSampleRate = this.micCtx.sampleRate;
    this.playCtx = new AudioContext({ sampleRate: OUTPUT_SAMPLE_RATE });

    // "token" = raw API key (local-dev fallback) uses `Token`; otherwise a
    // short-lived JWT uses `Bearer`.
    const useRawKey = config.auth_type === "token";
    const deepgram = new DeepgramClient(
      useRawKey ? { apiKey: config.token } : { accessToken: config.token }
    );
    this.connection = await deepgram.agent.v1.connect({
      Authorization: `${useRawKey ? "Token" : "Bearer"} ${config.token}`,
    });

    this.connection.on("open", () => {
      // Settings are sent after the Welcome message (below).
    });

    this.connection.on("message", (data: any) => {
      this.handleMessage(data, config.agent, inputSampleRate);
    });

    this.connection.on("error", (err: Error) => {
      this.cb.onError?.(err?.message || "Voice connection error");
    });

    this.connection.on("close", () => {
      if (!this.closed) this.cb.onClose?.();
    });

    this.connection.connect();
    await this.connection.waitForOpen();
  }

  private sendSettings(agentBlock: Record<string, unknown>, inputSampleRate: number) {
    this.connection.sendSettings({
      type: "Settings",
      audio: {
        input: { encoding: "linear16", sample_rate: inputSampleRate },
        output: {
          encoding: "linear16",
          sample_rate: OUTPUT_SAMPLE_RATE,
          container: "none",
        },
      },
      agent: agentBlock,
    });
  }

  private startMicPump(inputSampleRate: number) {
    if (!this.micCtx || !this.stream) return;
    this.source = this.micCtx.createMediaStreamSource(this.stream);
    this.processor = this.micCtx.createScriptProcessor(4096, 1, 1);
    this.processor.onaudioprocess = (e) => {
      if (this.closed || !this.connection) return;
      const input = e.inputBuffer.getChannelData(0);
      try {
        this.connection.sendMedia(floatTo16BitPCM(input));
      } catch {
        /* connection not ready / closed */
      }
    };
    // Route through a muted gain node so the processor runs without feedback.
    this.silentGain = this.micCtx.createGain();
    this.silentGain.gain.value = 0;
    this.source.connect(this.processor);
    this.processor.connect(this.silentGain);
    this.silentGain.connect(this.micCtx.destination);
    void inputSampleRate;
  }

  private enqueueAudio(bytes: ArrayBuffer) {
    if (!this.playCtx) return;
    const int16 = new Int16Array(bytes);
    if (int16.length === 0) return;
    const f32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) f32[i] = int16[i] / 0x8000;

    const buffer = this.playCtx.createBuffer(1, f32.length, OUTPUT_SAMPLE_RATE);
    buffer.copyToChannel(f32, 0);
    const src = this.playCtx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.playCtx.destination);

    const now = this.playCtx.currentTime;
    if (this.playHead < now) this.playHead = now;
    src.start(this.playHead);
    this.playHead += buffer.duration;
    this.playSources.push(src);
    src.onended = () => {
      this.playSources = this.playSources.filter((s) => s !== src);
    };
  }

  private clearPlayback() {
    for (const s of this.playSources) {
      try {
        s.stop();
      } catch {
        /* already stopped */
      }
    }
    this.playSources = [];
    this.playHead = 0;
  }

  private async handleMessage(
    data: any,
    agentBlock: Record<string, unknown>,
    inputSampleRate: number
  ) {
    // Binary audio chunk from the agent.
    if (data instanceof ArrayBuffer) {
      this.enqueueAudio(data);
      return;
    }
    if (typeof Blob !== "undefined" && data instanceof Blob) {
      this.enqueueAudio(await data.arrayBuffer());
      return;
    }
    if (!data || typeof data !== "object" || !("type" in data)) return;

    switch (data.type) {
      case "Welcome":
        this.sendSettings(agentBlock, inputSampleRate);
        break;
      case "SettingsApplied":
        this.startMicPump(inputSampleRate);
        this.cb.onState?.("listening");
        break;
      case "ConversationText":
        if (data.content) {
          this.cb.onCaption?.({
            role: data.role === "assistant" ? "assistant" : "user",
            text: data.content,
          });
        }
        break;
      case "UserStartedSpeaking":
        // Barge-in: stop whatever the agent is saying.
        this.clearPlayback();
        this.cb.onState?.("listening");
        break;
      case "AgentThinking":
        this.cb.onState?.("thinking");
        break;
      case "AgentStartedSpeaking":
        this.cb.onState?.("speaking");
        break;
      case "FunctionCallRequest":
        await this.handleFunctionCalls(data.functions || []);
        break;
      case "Error":
        this.cb.onError?.(data.description || data.message || "Agent error");
        break;
      default:
        break;
    }
  }

  private async handleFunctionCalls(functions: any[]) {
    for (const fn of functions) {
      if (!fn?.client_side) continue;
      let args: any = {};
      try {
        args = fn.arguments ? JSON.parse(fn.arguments) : {};
      } catch {
        args = {};
      }
      let content = "";
      try {
        content = await this.runFunction(fn.name, args);
      } catch (err: any) {
        content = JSON.stringify({
          ok: false,
          error: err?.message || "function failed",
        });
      }
      try {
        this.connection.sendFunctionCallResponse({
          type: "FunctionCallResponse",
          id: fn.id,
          name: fn.name,
          content,
        });
      } catch {
        /* connection closed */
      }
    }
  }

  private async runFunction(name: string, args: any): Promise<string> {
    if (name === "create_care_request") {
      const transcript = String(args.transcript || "").trim();
      if (!transcript) {
        return JSON.stringify({ ok: false, error: "empty transcript" });
      }
      const req = await createRequest(this.patientId, transcript);
      this.cb.onRequest?.(req);
      if (req.requires_confirmation) this.pendingConfirmId = req.request_id;
      return JSON.stringify({
        ok: true,
        request_id: req.request_id,
        category: req.category,
        urgency: req.urgency,
        requires_confirmation: req.requires_confirmation,
        spoken_response: req.spoken_response,
      });
    }

    if (name === "confirm_action") {
      const requestId = String(args.request_id || this.pendingConfirmId || "");
      const confirmed = Boolean(args.confirmed);
      if (!requestId) {
        return JSON.stringify({ ok: false, error: "missing request_id" });
      }
      const result = await confirmTask(requestId, confirmed);
      this.pendingConfirmId = null;
      this.cb.onConfirmResult?.(result.status, result.spoken_response);
      return JSON.stringify({ ok: true, ...result });
    }

    return JSON.stringify({ ok: false, error: `unknown function ${name}` });
  }

  // Allow the UI Yes/No buttons to drive confirmation while the agent is live.
  async confirmViaButton(confirmed: boolean): Promise<void> {
    if (!this.pendingConfirmId) return;
    const requestId = this.pendingConfirmId;
    const result = await confirmTask(requestId, confirmed);
    this.pendingConfirmId = null;
    this.cb.onConfirmResult?.(result.status, result.spoken_response);
    // Let the agent know so it can respond in-conversation.
    try {
      this.connection?.sendInjectUserMessage({
        type: "InjectUserMessage",
        content: confirmed
          ? "Yes, go ahead with that."
          : "No, please cancel that.",
      });
    } catch {
      /* best effort */
    }
  }

  async stop(): Promise<void> {
    this.closed = true;
    this.clearPlayback();
    try {
      this.processor?.disconnect();
      this.source?.disconnect();
      this.silentGain?.disconnect();
    } catch {
      /* ignore */
    }
    this.stream?.getTracks().forEach((t) => t.stop());
    try {
      await this.micCtx?.close();
    } catch {
      /* ignore */
    }
    try {
      await this.playCtx?.close();
    } catch {
      /* ignore */
    }
    try {
      this.connection?.close();
    } catch {
      /* ignore */
    }
    this.connection = null;
  }
}
