import type { CareRequest, Status } from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
export const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws";

export async function createRequest(
  patientId: string,
  transcript: string
): Promise<CareRequest> {
  const res = await fetch(`${API_BASE}/api/requests`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ patient_id: patientId, transcript }),
  });
  if (!res.ok) throw new Error(`createRequest failed: ${res.status}`);
  return res.json();
}

export async function createRequestAudio(
  patientId: string,
  audio: Blob
): Promise<CareRequest> {
  const form = new FormData();
  form.append("patient_id", patientId);
  form.append("audio", audio, "recording.webm");
  const res = await fetch(`${API_BASE}/api/requests`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(`createRequestAudio failed: ${res.status}`);
  return res.json();
}

export async function listRequests(): Promise<CareRequest[]> {
  const res = await fetch(`${API_BASE}/api/requests`, { cache: "no-store" });
  if (!res.ok) throw new Error(`listRequests failed: ${res.status}`);
  return res.json();
}

export async function updateStatus(
  requestId: string,
  status: Status
): Promise<CareRequest> {
  const res = await fetch(`${API_BASE}/api/requests/${requestId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  if (!res.ok) throw new Error(`updateStatus failed: ${res.status}`);
  return res.json();
}

export async function confirmTask(
  requestId: string,
  confirmed: boolean
): Promise<{ status: string; detail?: string; spoken_response?: string }> {
  const res = await fetch(`${API_BASE}/api/tasks/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_id: requestId, confirmed }),
  });
  if (!res.ok) throw new Error(`confirmTask failed: ${res.status}`);
  return res.json();
}

export async function synthesizeSpeech(text: string): Promise<Blob> {
  const res = await fetch(`${API_BASE}/api/speech/synthesize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new Error(`synthesizeSpeech failed: ${res.status}`);
  return res.blob();
}

export async function generateSummary(patientId: string): Promise<string> {
  const res = await fetch(`${API_BASE}/api/summaries/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ patient_id: patientId }),
  });
  if (!res.ok) throw new Error(`generateSummary failed: ${res.status}`);
  const data = await res.json();
  return data.summary;
}
