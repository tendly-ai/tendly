"use client";

import { useEffect, useState } from "react";
import { listRequests, updateStatus, WS_URL } from "../../lib/api";
import type { CareRequest, Status, Urgency } from "../../lib/types";

const URGENCY_COLOR: Record<Urgency, string> = {
  emergency: "#dc2626",
  high: "#ea580c",
  medium: "#ca8a04",
  low: "#16a34a",
};

// Scaffold caregiver dashboard. The Caregiver-Dashboard feature owns the full
// §3.2 build (richer cards, animations, patient context panel, etc.).
export default function Dashboard() {
  const [requests, setRequests] = useState<CareRequest[]>([]);

  async function refresh() {
    setRequests(await listRequests());
  }

  useEffect(() => {
    refresh();
    const ws = new WebSocket(WS_URL);
    ws.onmessage = () => refresh();
    return () => ws.close();
  }, []);

  async function setStatus(id: string, status: Status) {
    await updateStatus(id, status);
    refresh();
  }

  return (
    <main style={{ maxWidth: 900, margin: "32px auto", padding: 24 }}>
      <h1>Caregiver Dashboard</h1>
      <p style={{ color: "#475067" }}>{requests.length} active requests</p>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {requests.map((r) => (
          <div
            key={r.request_id}
            style={{
              borderLeft: `8px solid ${URGENCY_COLOR[r.urgency]}`,
              background: "white",
              borderRadius: 10,
              padding: 16,
              opacity: r.status === "resolved" ? 0.6 : 1,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <strong>
                {r.patient_name} · Room {r.room_number}
              </strong>
              <span style={{ color: URGENCY_COLOR[r.urgency], fontWeight: 700 }}>
                {r.urgency.toUpperCase()} · {r.category}
              </span>
            </div>
            <p style={{ fontSize: 18, margin: "8px 0" }}>{r.summary}</p>
            <p style={{ color: "#475067", fontStyle: "italic" }}>“{r.transcript}”</p>
            <p style={{ color: "#475067" }}>Suggested: {r.suggested_action}</p>
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              {(["new", "in_progress", "resolved"] as Status[]).map((s) => (
                <button
                  key={s}
                  onClick={() => setStatus(r.request_id, s)}
                  style={{
                    padding: "6px 12px",
                    borderRadius: 6,
                    border: "1px solid #cbd5e1",
                    background: r.status === s ? "#1f2937" : "white",
                    color: r.status === s ? "white" : "#1f2937",
                    cursor: "pointer",
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </main>
  );
}
