"use client";

import { useState, useRef, useCallback } from "react";
import { createRequest, createRequestAudio, confirmTask } from "../../lib/api";
import type { CareRequest } from "../../lib/types";
import styles from "./patient.module.css";

type UIState =
  | "idle"
  | "recording"
  | "processing"
  | "confirm"
  | "confirmed"
  | "error";

const PATIENTS: { id: string; name: string }[] = [
  { id: "patient_001", name: "Mary Johnson" },
  { id: "patient_002", name: "Robert Chen" },
  { id: "patient_003", name: "Gloria Reyes" },
];

export default function PatientPage() {
  const [state, setState] = useState<UIState>("idle");
  const [patientId, setPatientId] = useState("patient_001");
  const [req, setReq] = useState<CareRequest | null>(null);
  const [message, setMessage] = useState("");
  const [transcript, setTranscript] = useState("");
  const [showTextInput, setShowTextInput] = useState(false);
  const [textValue, setTextValue] = useState("");

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const resetToIdle = useCallback(() => {
    setState("idle");
    setReq(null);
    setMessage("");
    setTranscript("");
    setTextValue("");
    setShowTextInput(false);
  }, []);

  /* ---- Audio recording ---- */

  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream, {
        mimeType: MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
          ? "audio/webm;codecs=opus"
          : "audio/webm",
      });
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        await submitAudio(blob);
      };
      recorder.start();
      mediaRecorderRef.current = recorder;
      setState("recording");
    } catch {
      setMessage(
        "Could not access your microphone. Please check permissions, or use the text option below."
      );
      setState("error");
    }
  }, [patientId]); // eslint-disable-line react-hooks/exhaustive-deps

  const stopRecording = useCallback(() => {
    if (
      mediaRecorderRef.current &&
      mediaRecorderRef.current.state !== "inactive"
    ) {
      mediaRecorderRef.current.stop();
      setState("processing");
    }
  }, []);

  const toggleRecording = useCallback(() => {
    if (state === "recording") {
      stopRecording();
    } else {
      startRecording();
    }
  }, [state, startRecording, stopRecording]);

  /* ---- Submit helpers ---- */

  async function submitAudio(blob: Blob) {
    setState("processing");
    try {
      const r = await createRequestAudio(patientId, blob);
      handleResponse(r);
    } catch {
      setMessage("Sorry, something went wrong. Please try again.");
      setState("error");
    }
  }

  async function submitText(text: string) {
    if (!text.trim()) return;
    setState("processing");
    try {
      const r = await createRequest(patientId, text.trim());
      handleResponse(r);
    } catch {
      setMessage("Sorry, something went wrong. Please try again.");
      setState("error");
    }
  }

  function handleResponse(r: CareRequest) {
    setReq(r);
    setTranscript(r.transcript);
    if (r.requires_confirmation && r.confirmation_prompt) {
      setMessage(r.confirmation_prompt);
      setState("confirm");
    } else {
      setMessage("Got it! A caregiver has been notified.");
      setState("confirmed");
    }
  }

  /* ---- Confirmation ---- */

  async function onConfirm(ok: boolean) {
    if (!req) return;
    setState("processing");
    try {
      await confirmTask(req.request_id, ok);
      setMessage(
        ok ? "Opening that for you now." : "No problem, cancelled."
      );
      setState("confirmed");
    } catch {
      setMessage("Sorry, something went wrong. Please try again.");
      setState("error");
    }
  }

  /* ---- Render helpers ---- */

  const containerClass = [
    styles.container,
    state === "idle"
      ? styles.containerIdle
      : state === "recording"
        ? styles.containerRecording
        : state === "processing"
          ? styles.containerProcessing
          : state === "confirm"
            ? styles.containerConfirm
            : state === "confirmed"
              ? styles.containerConfirmed
              : styles.containerError,
  ].join(" ");

  return (
    <main className={containerClass}>
      {/* Patient selector (small, top-right) */}
      <div className={styles.patientSelector}>
        <label htmlFor="patient-select" className={styles.patientLabel}>
          Patient:
        </label>
        <select
          id="patient-select"
          className={styles.patientSelect}
          value={patientId}
          onChange={(e) => setPatientId(e.target.value)}
          disabled={state !== "idle"}
        >
          {PATIENTS.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>

      <div className={styles.card}>
        {/* ===== IDLE state ===== */}
        {state === "idle" && (
          <>
            <h1 className={styles.title}>
              Hello,{" "}
              {PATIENTS.find((p) => p.id === patientId)?.name?.split(" ")[0] ??
                "there"}
              !
            </h1>
            <p className={styles.subtitle}>
              Press the button and tell us what you need.
            </p>

            <button
              className={`${styles.talkButton} ${styles.talkButtonIdle}`}
              onClick={toggleRecording}
              aria-label="Talk to your caregiver"
            >
              <span className={styles.buttonIcon}>🎙</span>
              <span className={styles.buttonLabel}>Talk to Your Caregiver</span>
            </button>

            {/* Text fallback */}
            {!showTextInput ? (
              <button
                className={styles.fallbackToggle}
                onClick={() => setShowTextInput(true)}
              >
                Or type your request instead
              </button>
            ) : (
              <div className={styles.textInputArea}>
                <input
                  className={styles.textInput}
                  value={textValue}
                  onChange={(e) => setTextValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") submitText(textValue);
                  }}
                  placeholder="Type your request here..."
                  autoFocus
                />
                <button
                  className={styles.textSubmit}
                  disabled={!textValue.trim()}
                  onClick={() => submitText(textValue)}
                >
                  Send
                </button>
              </div>
            )}
          </>
        )}

        {/* ===== RECORDING state ===== */}
        {state === "recording" && (
          <>
            <p className={styles.statusLabel} style={{ color: "#dc2626" }}>
              Listening...
            </p>
            <div className={styles.waveform}>
              {Array.from({ length: 7 }).map((_, i) => (
                <div key={i} className={styles.waveDot} />
              ))}
            </div>
            <button
              className={`${styles.talkButton} ${styles.talkButtonRecording}`}
              onClick={toggleRecording}
              aria-label="Stop recording"
            >
              <span className={styles.buttonIcon}>⏹</span>
              <span className={styles.buttonLabel}>Tap to Stop</span>
            </button>
          </>
        )}

        {/* ===== PROCESSING state ===== */}
        {state === "processing" && (
          <>
            <div className={styles.spinner} />
            <p className={styles.statusLabel} style={{ color: "#4b5563" }}>
              Thinking...
            </p>
          </>
        )}

        {/* ===== CONFIRM state ===== */}
        {state === "confirm" && (
          <>
            {transcript && (
              <div className={styles.transcript}>
                &ldquo;{transcript}&rdquo;
              </div>
            )}
            <p className={styles.messageText}>{message}</p>
            <div className={styles.confirmButtons}>
              <button
                className={styles.confirmYes}
                onClick={() => onConfirm(true)}
              >
                Yes
              </button>
              <button
                className={styles.confirmNo}
                onClick={() => onConfirm(false)}
              >
                No
              </button>
            </div>
          </>
        )}

        {/* ===== CONFIRMED state ===== */}
        {state === "confirmed" && (
          <>
            <div className={styles.checkmark}>&#10003;</div>
            {transcript && (
              <div className={styles.transcript}>
                &ldquo;{transcript}&rdquo;
              </div>
            )}
            <p
              className={`${styles.messageText} ${styles.messageTextSuccess}`}
            >
              {message}
            </p>
            <button className={styles.actionButton} onClick={resetToIdle}>
              New Request
            </button>
          </>
        )}

        {/* ===== ERROR state ===== */}
        {state === "error" && (
          <>
            <p className={`${styles.messageText} ${styles.messageTextError}`}>
              {message}
            </p>
            <button className={styles.retryButton} onClick={resetToIdle}>
              Try Again
            </button>
          </>
        )}
      </div>
    </main>
  );
}
