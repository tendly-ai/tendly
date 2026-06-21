"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import {
  createRequest,
  createRequestAudio,
  confirmTask,
  synthesizeSpeech,
  listRequests,
} from "../../lib/api";
import type { CareRequest } from "../../lib/types";
import { VoiceAgentSession, type AgentState, type Caption } from "../../lib/voice";
import { useAccount } from "../context/AccountContext";
import styles from "./patient.module.css";

type UIState =
  | "idle"
  | "connecting"
  | "conversing"
  | "recording"
  | "processing"
  | "confirm"
  | "confirmed"
  | "error";

const URGENCY_DOT: Record<string, string> = {
  emergency: "#ef4444",
  high:      "#f97316",
  medium:    "#eab308",
  low:       "#22c55e",
};

const STATUS_PILL_STYLE: Record<string, { background: string; color: string }> = {
  new:         { background: "#eff6ff", color: "#2563eb" },
  in_progress: { background: "#fefce8", color: "#a16207" },
  resolved:    { background: "#f0fdf4", color: "#16a34a" },
};

const CARE_TEAM = [
  { initials: "JP", name: "Jenny Park",    role: "RN · Charge Nurse", color: "#0284c7" },
  { initials: "CM", name: "Carlos Mendez", role: "Care Aide",          color: "#0284c7" },
];

function greeting() {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

function fmtDate() {
  return new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}

function fmtTime(iso: string) {
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function MicIcon() {
  return (
    <svg width="40" height="40" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2a3 3 0 0 1 3 3v7a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3z"/>
      <path d="M19 10v1a7 7 0 0 1-14 0v-1"/>
      <line x1="12" y1="19" x2="12" y2="22"/>
      <line x1="8" y1="22" x2="16" y2="22"/>
    </svg>
  );
}

function StopIcon() {
  return (
    <svg width="29" height="29" viewBox="0 0 24 24" fill="currentColor">
      <rect x="5" y="5" width="14" height="14" rx="2.5"/>
    </svg>
  );
}

export default function PatientPage() {
  const { account } = useAccount();
  const router      = useRouter();

  const [uiState, setUIState]         = useState<UIState>("idle");
  const [req, setReq]                 = useState<CareRequest | null>(null);
  const [message, setMessage]         = useState("");
  const [transcript, setTranscript]   = useState("");
  const [showText, setShowText]       = useState(false);
  const [textValue, setTextValue]     = useState("");
  const [speechReady, setSpeechReady] = useState(false);
  const [speechFailed, setSpeechFailed] = useState(false);
  const [recentReqs, setRecentReqs]   = useState<CareRequest[]>([]);

  const [agentState, setAgentState] = useState<AgentState>("connecting");
  const [captions, setCaptions]     = useState<Caption[]>([]);
  const [pendingReq, setPendingReq] = useState<CareRequest | null>(null);

  const mediaRef    = useRef<MediaRecorder | null>(null);
  const chunksRef   = useRef<Blob[]>([]);
  const audioRef    = useRef<HTMLAudioElement | null>(null);
  const audioUrlRef = useRef<string | null>(null);
  const sessionRef  = useRef<VoiceAgentSession | null>(null);

  useEffect(() => {
    if (!account) router.replace("/");
  }, [account, router]);

  useEffect(() => {
    if (!account) return;
    listRequests()
      .then(all => setRecentReqs(
        all.filter(r => r.patient_id === account.id)
           .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
           .slice(0, 4)
      ))
      .catch(() => {});
  }, [account, uiState]);

  const patientId = account?.id ?? "patient_001";
  const firstName = account?.name.split(" ")[0] ?? "there";

  const reset = useCallback(() => {
    setUIState("idle"); setReq(null); setMessage(""); setTranscript("");
    setTextValue(""); setShowText(false);
    setSpeechReady(false); setSpeechFailed(false);
    setCaptions([]); setPendingReq(null);
    audioRef.current?.pause(); audioRef.current = null;
    if (audioUrlRef.current) { URL.revokeObjectURL(audioUrlRef.current); audioUrlRef.current = null; }
  }, []);

  /* ---- Conversational voice agent ---- */

  const endConversation = useCallback(async () => {
    const session = sessionRef.current;
    sessionRef.current = null;
    if (session) await session.stop();
    reset();
  }, [reset]);

  const startConversation = useCallback(async () => {
    setCaptions([]); setPendingReq(null);
    setAgentState("connecting"); setUIState("connecting");

    const session = new VoiceAgentSession(patientId, {
      onState: (s) => setAgentState(s),
      onCaption: (c) =>
        setCaptions((prev) => [...prev.slice(-7), c]),
      onRequest: (r) => setPendingReq(r.requires_confirmation ? r : null),
      onConfirmResult: () => setPendingReq(null),
      onError: (msg) => {
        setMessage(msg || "Something went wrong with the voice connection.");
        setUIState("error");
        sessionRef.current?.stop();
        sessionRef.current = null;
      },
      onClose: () => {
        sessionRef.current = null;
        setUIState((cur) => (cur === "conversing" ? "idle" : cur));
      },
    });

    try {
      sessionRef.current = session;
      await session.start();
      setUIState("conversing");
    } catch (err) {
      sessionRef.current = null;
      await session.stop().catch(() => {});
      // Fall back to the single-shot recorder when the agent is unavailable.
      setMessage(
        "Live conversation isn't available right now. You can record a single request or type instead."
      );
      setUIState("error");
    }
  }, [patientId]);

  // Confirm via on-screen buttons while the agent is live.
  const confirmInConversation = useCallback(async (ok: boolean) => {
    await sessionRef.current?.confirmViaButton(ok);
    setPendingReq(null);
  }, []);

  // Stop any live session when leaving the page.
  useEffect(() => {
    return () => { sessionRef.current?.stop(); sessionRef.current = null; };
  }, []);

  const playTalkBack = useCallback(async (text: string) => {
    if (!text.trim()) return;
    setSpeechReady(false); setSpeechFailed(false);
    try {
      const blob = await synthesizeSpeech(text.trim());
      if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
      const url = URL.createObjectURL(blob);
      audioUrlRef.current = url;
      const player = new Audio(url);
      audioRef.current = player;
      setSpeechReady(true);
      await player.play();
    } catch { setSpeechFailed(true); }
  }, []);

  const replay = useCallback(() => {
    if (!audioRef.current) return;
    audioRef.current.currentTime = 0;
    audioRef.current.play().catch(() => setSpeechFailed(true));
  }, []);

  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream, {
        mimeType: MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "audio/webm",
      });
      chunksRef.current = [];
      recorder.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data); };
      recorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        setUIState("processing");
        try { handleResponse(await createRequestAudio(patientId, blob)); }
        catch { setMessage("Something went wrong. Please try again."); setUIState("error"); }
      };
      recorder.start();
      mediaRef.current = recorder;
      setUIState("recording");
    } catch {
      setMessage("Couldn't access microphone. Please check permissions or type below.");
      setUIState("error");
    }
  }, [patientId]); // eslint-disable-line react-hooks/exhaustive-deps

  const stopRecording = useCallback(() => {
    if (mediaRef.current && mediaRef.current.state !== "inactive") mediaRef.current.stop();
  }, []);

  const toggleRecording = useCallback(() => {
    uiState === "recording" ? stopRecording() : startRecording();
  }, [uiState, startRecording, stopRecording]);

  async function submitText(text: string) {
    if (!text.trim()) return;
    setUIState("processing");
    try { handleResponse(await createRequest(patientId, text.trim())); }
    catch { setMessage("Something went wrong. Please try again."); setUIState("error"); }
  }

  function handleResponse(r: CareRequest) {
    setReq(r); setTranscript(r.transcript);
    if (r.requires_confirmation && r.confirmation_prompt) {
      setMessage(r.confirmation_prompt); setUIState("confirm");
      playTalkBack(r.spoken_response || r.confirmation_prompt);
    } else {
      const msg = r.spoken_response || "Got it — I've let your caregiver know.";
      setMessage(msg); setUIState("confirmed"); playTalkBack(msg);
    }
  }

  async function onConfirm(ok: boolean) {
    if (!req) return;
    setUIState("processing");
    try {
      const result = await confirmTask(req.request_id, ok);
      const msg = result.spoken_response || (ok ? "On it." : "Cancelled.");
      setMessage(msg); setUIState("confirmed"); playTalkBack(msg);
    } catch { setMessage("Something went wrong."); setUIState("error"); }
  }

  if (!account) return null;

  return (
    <div className={styles.page}>

      {/* ── Left dark panel ── */}
      <div className={styles.leftPanel}>
        <div className={styles.panelTop}>
          <div className={styles.bigAvatar} style={{ background: account.color }}>
            {account.initials}
          </div>
          <div className={styles.panelGreeting}>{greeting()}</div>
          <div className={styles.panelName}>{firstName}.</div>
          <div className={styles.panelDate}>{fmtDate()}</div>
        </div>

        <div className={styles.panelDivider} />

        <div className={styles.panelSection}>
          <div className={styles.panelSectionLabel}>Your care team</div>
          <div className={styles.teamList}>
            {CARE_TEAM.map(t => (
              <div key={t.initials} className={styles.teamRow}>
                <div className={styles.teamAvatar} style={{ background: t.color }}>{t.initials}</div>
                <div>
                  <div className={styles.teamName}>{t.name}</div>
                  <div className={styles.teamRole}>{t.role}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className={styles.panelDivider} />

        <div className={styles.panelSection}>
          <div className={styles.panelSectionLabel}>Recent activity</div>
          {recentReqs.length === 0
            ? <p className={styles.activityEmpty}>No recent requests.</p>
            : (
              <div className={styles.activityList}>
                {recentReqs.map(r => (
                  <div key={r.request_id} className={styles.activityItem}>
                    <div className={styles.activityMeta}>
                      <span className={styles.activityDot} style={{ background: URGENCY_DOT[r.urgency] }} />
                      <span className={styles.activitySummary}>{r.summary}</span>
                    </div>
                    <div className={styles.activityFooter}>
                      <span className={styles.activityTime}>{fmtTime(r.created_at)}</span>
                      <span className={styles.activityPill} style={STATUS_PILL_STYLE[r.status]}>
                        {r.status === "in_progress" ? "In Progress" : r.status.charAt(0).toUpperCase() + r.status.slice(1)}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )
          }
        </div>
      </div>

      {/* ── Right light panel ── */}
      <div className={styles.rightPanel}>
        <div className={styles.actionCard}>

          {uiState === "idle" && (<>
            <p className={styles.cardPrompt}>What do you need?</p>
            <button className={styles.talkBtn} onClick={startConversation} aria-label="Talk with Tendly">
              <MicIcon />
            </button>
            <p className={styles.talkHint}>Talk with Tendly</p>
            {!showText
              ? <button className={styles.typeToggle} onClick={() => setShowText(true)}>or type your request</button>
              : <div className={styles.textArea}>
                  <input
                    className={styles.textInput}
                    value={textValue}
                    onChange={e => setTextValue(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter") submitText(textValue); }}
                    placeholder="Type your request…"
                    autoFocus
                  />
                  <button className={styles.sendBtn} disabled={!textValue.trim()} onClick={() => submitText(textValue)}>
                    Send
                  </button>
                </div>
            }
          </>)}

          {uiState === "connecting" && (<>
            <div className={styles.spinner} />
            <p className={styles.processingLabel}>Connecting…</p>
          </>)}

          {uiState === "conversing" && (<>
            <p className={styles.recordingLabel}>
              {agentState === "speaking"
                ? "Tendly is speaking…"
                : agentState === "thinking"
                  ? "Thinking…"
                  : "Listening…"}
            </p>
            <div className={styles.waveform}>
              {Array.from({ length: 7 }).map((_, i) => <div key={i} className={styles.waveDot} />)}
            </div>

            {captions.length > 0 && (
              <div className={styles.captionList}>
                {captions.map((c, i) => (
                  <p
                    key={i}
                    className={c.role === "assistant" ? styles.captionAgent : styles.captionUser}
                  >
                    {c.text}
                  </p>
                ))}
              </div>
            )}

            {pendingReq && (
              <div className={styles.confirmRow}>
                <button className={styles.confirmYes} onClick={() => confirmInConversation(true)}>Yes, go ahead</button>
                <button className={styles.confirmNo}  onClick={() => confirmInConversation(false)}>No, cancel</button>
              </div>
            )}

            <button className={`${styles.talkBtn} ${styles.talkBtnRec}`} onClick={endConversation} aria-label="End conversation">
              <StopIcon />
            </button>
            <p className={styles.talkHint}>Tap to end</p>
          </>)}

          {uiState === "recording" && (<>
            <p className={styles.recordingLabel}>Listening…</p>
            <div className={styles.waveform}>
              {Array.from({ length: 7 }).map((_, i) => <div key={i} className={styles.waveDot} />)}
            </div>
            <button className={`${styles.talkBtn} ${styles.talkBtnRec}`} onClick={toggleRecording} aria-label="Stop">
              <StopIcon />
            </button>
            <p className={styles.talkHint}>Tap to stop</p>
          </>)}

          {uiState === "processing" && (<>
            <div className={styles.spinner} />
            <p className={styles.processingLabel}>Processing…</p>
          </>)}

          {uiState === "confirm" && (<>
            {transcript && <p className={styles.quoteText}>&ldquo;{transcript}&rdquo;</p>}
            <p className={styles.responseMsg}>{message}</p>
            <TalkBack ready={speechReady} failed={speechFailed} onReplay={replay} styles={styles} />
            <div className={styles.confirmRow}>
              <button className={styles.confirmYes} onClick={() => onConfirm(true)}>Yes, go ahead</button>
              <button className={styles.confirmNo}  onClick={() => onConfirm(false)}>No, cancel</button>
            </div>
          </>)}

          {uiState === "confirmed" && (<>
            <div className={styles.checkmark}>✓</div>
            {transcript && <p className={styles.quoteText}>&ldquo;{transcript}&rdquo;</p>}
            <p className={`${styles.responseMsg} ${styles.responseMsgOk}`}>{message}</p>
            <TalkBack ready={speechReady} failed={speechFailed} onReplay={replay} styles={styles} />
            <button className={styles.newReqBtn} onClick={reset}>New request</button>
          </>)}

          {uiState === "error" && (<>
            <p className={`${styles.responseMsg} ${styles.responseMsgErr}`}>{message}</p>
            <div className={styles.confirmRow}>
              <button className={styles.confirmYes} onClick={() => { reset(); startRecording(); }}>Record instead</button>
              <button className={styles.confirmNo} onClick={reset}>Try again</button>
            </div>
          </>)}

        </div>
      </div>

    </div>
  );
}

function TalkBack({ ready, failed, onReplay, styles }: {
  ready: boolean; failed: boolean; onReplay: () => void;
  styles: Record<string, string>;
}) {
  if (ready)  return <button className={styles.replayBtn} onClick={onReplay}>↺ Replay</button>;
  if (failed) return <p className={styles.speechNote}>Voice unavailable</p>;
  return null;
}
