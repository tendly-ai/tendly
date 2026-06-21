"use client";

import { useState } from "react";
import { createRequest, confirmTask } from "../../lib/api";
import type { CareRequest } from "../../lib/types";

type UIState = "idle" | "recording" | "processing" | "confirm" | "confirmed" | "error";

// Scaffold patient UI. The Patient-Interface feature owns the full §3.1 build
// (large accessible button, MediaRecorder audio capture, waveform, etc.).
export default function PatientPage() {
  const [state, setState] = useState<UIState>("idle");
  const [text, setText] = useState("");
  const [req, setReq] = useState<CareRequest | null>(null);
  const [message, setMessage] = useState("");

  const patientId = "patient_001";

  async function submit(transcript: string) {
    setState("processing");
    try {
      const r = await createRequest(patientId, transcript);
      setReq(r);
      if (r.requires_confirmation && r.confirmation_prompt) {
        setMessage(r.confirmation_prompt);
        setState("confirm");
      } else {
        setMessage("Got it! A caregiver has been notified.");
        setState("confirmed");
      }
    } catch (e) {
      setMessage("Sorry, something went wrong. Please try again.");
      setState("error");
    }
  }

  async function onConfirm(ok: boolean) {
    if (!req) return;
    setState("processing");
    await confirmTask(req.request_id, ok);
    setMessage(ok ? "Okay, doing that for you now." : "No problem, cancelled.");
    setState("confirmed");
  }

  return (
    <main style={{ maxWidth: 640, margin: "60px auto", padding: 24, textAlign: "center" }}>
      <h1 style={{ fontSize: 32 }}>Talk to Your Caregiver</h1>

      {state === "confirm" ? (
        <div>
          <p style={{ fontSize: 24 }}>{message}</p>
          <div style={{ display: "flex", gap: 16, justifyContent: "center", marginTop: 24 }}>
            <button onClick={() => onConfirm(true)} style={btn("#16a34a")}>Yes</button>
            <button onClick={() => onConfirm(false)} style={btn("#dc2626")}>No</button>
          </div>
        </div>
      ) : state === "confirmed" || state === "error" ? (
        <div>
          <p style={{ fontSize: 24 }}>{message}</p>
          <button onClick={() => { setState("idle"); setText(""); }} style={btn("#2563eb")}>
            Done
          </button>
        </div>
      ) : (
        <div>
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Type or speak your request…"
            style={{ fontSize: 20, padding: 12, width: "100%", borderRadius: 8, border: "1px solid #cbd5e1" }}
          />
          <button
            disabled={state === "processing" || !text}
            onClick={() => submit(text)}
            style={{ ...btn("#2563eb"), width: 220, height: 120, borderRadius: 20, marginTop: 24, fontSize: 22 }}
          >
            {state === "processing" ? "Thinking…" : "Send"}
          </button>
        </div>
      )}
    </main>
  );
}

function btn(bg: string): React.CSSProperties {
  return {
    padding: "16px 28px",
    fontSize: 22,
    background: bg,
    color: "white",
    border: "none",
    borderRadius: 12,
    cursor: "pointer",
  };
}
