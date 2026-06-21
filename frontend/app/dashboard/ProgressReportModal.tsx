"use client";

import { useEffect, useState } from "react";
import {
  generateSummary,
  listPatients,
  lookupContact,
} from "../../lib/api";
import type { FamilyContact, PatientProfile } from "../../lib/types";
import styles from "./dashboard.module.css";

type Recipient = { name: string; email: string; relation?: string };

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString();
}

// The LLM occasionally still emits a greeting/sign-off despite the prompt.
// Strip them so the modal can add a recipient-specific greeting exactly once.
function stripGreetingAndSignoff(text: string): string {
  let t = text.trim();
  // Leading greeting line: "Hi Sarah," / "Hello Sarah," / "Dear Sarah,"
  t = t.replace(/^(hi|hello|hey|dear|good (morning|afternoon|evening))\b[^\n]*,?\s*\n+/i, "");
  // Trailing sign-off block: "Warmly,\nThe Tendly Care Team" etc.
  t = t.replace(
    /\n+\s*(warmly|sincerely|best|kind regards|regards|with love|love|take care|thank you|thanks)\b[\s\S]*$/i,
    ""
  );
  return t.trim();
}

export default function ProgressReportModal({ onClose }: { onClose: () => void }) {
  const [patients, setPatients] = useState<PatientProfile[]>([]);
  const [patientId, setPatientId] = useState<string>("");
  const [recipient, setRecipient] = useState<Recipient | null>(null);

  const [searchName, setSearchName] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");

  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState("");
  const [sent, setSent] = useState(false);

  useEffect(() => {
    listPatients()
      .then((ps) => {
        setPatients(ps);
        if (ps.length > 0) setPatientId(ps[0].patient_id);
      })
      .catch(() => setPatients([]));
  }, []);

  const patient = patients.find((p) => p.patient_id === patientId) || null;
  const familyOptions: FamilyContact[] = (patient?.family_contacts || []).filter(
    (c) => !!c.email
  );

  // Reset downstream state when patient changes.
  useEffect(() => {
    setRecipient(null);
    setSubject("");
    setBody("");
    setSent(false);
    setGenError("");
  }, [patientId]);

  async function handleSearch() {
    const name = searchName.trim();
    if (!name) return;
    setSearching(true);
    setSearchError("");
    try {
      const c = await lookupContact(name);
      if (c.email) {
        setRecipient({ name: c.name || name, email: c.email, relation: "contact" });
      } else {
        setSearchError(
          c.name
            ? `Found ${c.name}, but they have no email on file.`
            : `No contact named "${name}" was found.`
        );
      }
    } catch {
      setSearchError("Contact lookup failed.");
    } finally {
      setSearching(false);
    }
  }

  async function handleGenerate() {
    if (!patient || !recipient) return;
    setGenerating(true);
    setGenError("");
    setSent(false);
    try {
      const summary = await generateSummary(patient.patient_id, isoDaysAgo(7));
      const cleaned = stripGreetingAndSignoff(summary);
      setSubject(`Weekly update on ${patient.name}`);
      setBody(
        `Hi ${recipient.name.split(" ")[0]},\n\n${cleaned}\n\nWarmly,\nThe Tendly Care Team`
      );
    } catch {
      setGenError("Could not generate the summary. Please try again.");
    } finally {
      setGenerating(false);
    }
  }

  function handleOpenEmail() {
    if (!recipient || !body) return;
    const mailto =
      `mailto:${encodeURIComponent(recipient.email)}` +
      `?subject=${encodeURIComponent(subject)}` +
      `&body=${encodeURIComponent(body)}`;

    // In Electron, route through the IPC bridge so the OS mail client opens
    // reliably. In a plain browser, fall back to an anchor click.
    const electron = (window as unknown as {
      electron?: { openExternal?: (url: string) => void };
    }).electron;
    if (electron?.openExternal) {
      electron.openExternal(mailto);
    } else {
      const a = document.createElement("a");
      a.href = mailto;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    }
    setSent(true);
  }

  return (
    <div className={styles.modalOverlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <h2 className={styles.modalTitle}>Send progress report</h2>
          <button className={styles.modalClose} onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className={styles.modalBody}>
          {/* Step 1: patient */}
          <label className={styles.field}>
            <span className={styles.fieldLabel}>Resident</span>
            <select
              className={styles.select}
              value={patientId}
              onChange={(e) => setPatientId(e.target.value)}
            >
              {patients.map((p) => (
                <option key={p.patient_id} value={p.patient_id}>
                  {p.name} · Rm {p.room_number}
                </option>
              ))}
            </select>
          </label>

          {/* Step 2: recipient */}
          <div className={styles.field}>
            <span className={styles.fieldLabel}>Send to</span>
            {familyOptions.length > 0 ? (
              <div className={styles.recipientList}>
                {familyOptions.map((c, i) => (
                  <button
                    key={c.email}
                    className={`${styles.recipientChip} ${
                      recipient?.email === c.email ? styles.recipientChipActive : ""
                    }`}
                    onClick={() =>
                      setRecipient({ name: c.name, email: c.email as string, relation: c.relation })
                    }
                  >
                    <span className={styles.recipientName}>
                      {c.name}
                      {i === 0 && (
                        <span className={styles.primaryBadge}>Primary contact</span>
                      )}
                    </span>
                    <span className={styles.recipientMeta}>
                      {c.relation} · {c.email}
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <p className={styles.hint}>No family contacts with an email on file.</p>
            )}

            <div className={styles.searchRow}>
              <input
                className={styles.input}
                placeholder="Search another contact by name…"
                value={searchName}
                onChange={(e) => setSearchName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleSearch();
                }}
              />
              <button
                className={styles.secondaryBtn}
                onClick={handleSearch}
                disabled={searching || !searchName.trim()}
              >
                {searching ? "…" : "Find"}
              </button>
            </div>
            {searchError && <p className={styles.errorText}>{searchError}</p>}
            {recipient && (
              <p className={styles.selectedRecipient}>
                Selected: <strong>{recipient.name}</strong> ({recipient.email})
              </p>
            )}
          </div>

          {/* Step 3: preview */}
          {(subject || body) && (
            <>
              <label className={styles.field}>
                <span className={styles.fieldLabel}>Subject</span>
                <input
                  className={styles.input}
                  value={subject}
                  onChange={(e) => setSubject(e.target.value)}
                />
              </label>
              <label className={styles.field}>
                <span className={styles.fieldLabel}>Message</span>
                <textarea
                  className={styles.textarea}
                  rows={9}
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                />
              </label>
            </>
          )}

          {genError && <p className={styles.errorText}>{genError}</p>}
          {sent && (
            <p className={styles.successText}>
              Opened your email app with the report ready to send.
            </p>
          )}
        </div>

        <div className={styles.modalFooter}>
          <button className={styles.secondaryBtn} onClick={onClose}>
            Close
          </button>
          {!subject && !body ? (
            <button
              className={styles.primaryBtn}
              onClick={handleGenerate}
              disabled={!recipient || generating}
            >
              {generating ? "Generating…" : "Generate preview"}
            </button>
          ) : (
            <div className={styles.footerActions}>
              <button
                className={styles.secondaryBtn}
                onClick={handleGenerate}
                disabled={generating}
              >
                {generating ? "…" : "Regenerate"}
              </button>
              <button
                className={styles.primaryBtn}
                onClick={handleOpenEmail}
                disabled={!recipient || !body}
              >
                Open email
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
