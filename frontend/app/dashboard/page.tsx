"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { listRequests, updateStatus, WS_URL } from "../../lib/api";
import type { CareRequest, Status, Urgency } from "../../lib/types";
import styles from "./dashboard.module.css";

/* ---- urgency sort rank (lower = more urgent) ---- */
const URGENCY_RANK: Record<Urgency, number> = {
  emergency: 0,
  high: 1,
  medium: 2,
  low: 3,
};

/* ---- human-readable labels ---- */
const URGENCY_LABEL: Record<Urgency, string> = {
  emergency: "EMERGENCY",
  high: "HIGH",
  medium: "MEDIUM",
  low: "LOW",
};

const CATEGORY_LABEL: Record<string, string> = {
  urgent_medical: "Urgent Medical",
  in_person_caregiver: "In-Person Caregiver",
  routine_comfort: "Routine Comfort",
  automated_task: "Automated Task",
  family_communication: "Family Communication",
  general_conversation: "General Conversation",
};

const STATUS_LABEL: Record<Status, string> = {
  new: "New",
  in_progress: "In Progress",
  resolved: "Resolved",
};

/* ---- CSS class lookups ---- */
const URGENCY_BORDER: Record<Urgency, string> = {
  emergency: styles.urgencyEmergency,
  high: styles.urgencyHigh,
  medium: styles.urgencyMedium,
  low: styles.urgencyLow,
};

const URGENCY_BADGE: Record<Urgency, string> = {
  emergency: styles.badgeEmergency,
  high: styles.badgeHigh,
  medium: styles.badgeMedium,
  low: styles.badgeLow,
};

const STATUS_CLASS: Record<Status, string> = {
  new: styles.statusNew,
  in_progress: styles.statusInProgress,
  resolved: styles.statusResolved,
};

/* ---- sorting ---- */
function sortRequests(list: CareRequest[]): CareRequest[] {
  return [...list].sort((a, b) => {
    const aResolved = a.status === "resolved" ? 1 : 0;
    const bResolved = b.status === "resolved" ? 1 : 0;
    if (aResolved !== bResolved) return aResolved - bResolved;

    const urgDiff = URGENCY_RANK[a.urgency] - URGENCY_RANK[b.urgency];
    if (urgDiff !== 0) return urgDiff;

    return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
  });
}

/* ---- format timestamp ---- */
function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/* ---- status flow: allowed next states ---- */
const NEXT_STATUSES: Record<Status, Status[]> = {
  new: ["in_progress"],
  in_progress: ["resolved"],
  resolved: [],
};

/* ============================================================
   Dashboard component
   ============================================================ */
export default function Dashboard() {
  const [requests, setRequests] = useState<CareRequest[]>([]);
  const [wsConnected, setWsConnected] = useState(false);
  const highlightedIds = useRef<Set<string>>(new Set());
  const knownIds = useRef<Set<string>>(new Set());

  /* Fetch all requests and update state */
  const refresh = useCallback(async () => {
    try {
      const data = await listRequests();
      setRequests((prev) => {
        /* Detect new high-urgency requests for highlight */
        const prevIds = new Set(prev.map((r) => r.request_id));
        for (const r of data) {
          if (
            !prevIds.has(r.request_id) &&
            !knownIds.current.has(r.request_id) &&
            (r.urgency === "emergency" || r.urgency === "high")
          ) {
            highlightedIds.current.add(r.request_id);
            setTimeout(() => {
              highlightedIds.current.delete(r.request_id);
            }, 4000);
          }
          knownIds.current.add(r.request_id);
        }
        return sortRequests(data);
      });
    } catch {
      /* network error — keep existing state */
    }
  }, []);

  /* WebSocket setup */
  useEffect(() => {
    refresh();

    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout>;

    function connect() {
      ws = new WebSocket(WS_URL);
      ws.onopen = () => setWsConnected(true);
      ws.onclose = () => {
        setWsConnected(false);
        reconnectTimer = setTimeout(connect, 3000);
      };
      ws.onerror = () => ws?.close();
      ws.onmessage = () => refresh();
    }

    connect();

    return () => {
      clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, [refresh]);

  /* Status change handler */
  async function handleStatusChange(requestId: string, newStatus: Status) {
    try {
      await updateStatus(requestId, newStatus);
      await refresh();
    } catch {
      /* allow ws broadcast to fix state */
    }
  }

  /* Split active vs resolved */
  const activeRequests = requests.filter((r) => r.status !== "resolved");
  const resolvedRequests = requests.filter((r) => r.status === "resolved");

  return (
    <main className={styles.wrapper}>
      {/* Header */}
      <header className={styles.header}>
        <h1 className={styles.title}>
          Caregiver Dashboard
          {wsConnected && (
            <span className={styles.liveIndicator}>
              <span className={styles.liveDot} /> Live
            </span>
          )}
        </h1>
        <p className={styles.subtitle}>
          {activeRequests.length} active request{activeRequests.length !== 1 ? "s" : ""}
          {resolvedRequests.length > 0 &&
            ` · ${resolvedRequests.length} resolved`}
        </p>
      </header>

      {/* Active requests */}
      {activeRequests.length === 0 && resolvedRequests.length === 0 && (
        <div className={styles.emptyState}>
          No requests yet. Waiting for patients&hellip;
        </div>
      )}

      {activeRequests.length > 0 && (
        <>
          <div className={styles.sectionHeading}>Active Requests</div>
          <div className={styles.cardList}>
            {activeRequests.map((r) => (
              <RequestCard
                key={r.request_id}
                request={r}
                highlighted={highlightedIds.current.has(r.request_id)}
                onStatusChange={handleStatusChange}
              />
            ))}
          </div>
        </>
      )}

      {/* Resolved requests */}
      {resolvedRequests.length > 0 && (
        <>
          <div className={styles.sectionHeading}>Resolved</div>
          <div className={styles.cardList}>
            {resolvedRequests.map((r) => (
              <RequestCard
                key={r.request_id}
                request={r}
                highlighted={false}
                onStatusChange={handleStatusChange}
              />
            ))}
          </div>
        </>
      )}
    </main>
  );
}

/* ============================================================
   Request Card
   ============================================================ */
interface RequestCardProps {
  request: CareRequest;
  highlighted: boolean;
  onStatusChange: (id: string, status: Status) => void;
}

function RequestCard({ request: r, highlighted, onStatusChange }: RequestCardProps) {
  const isResolved = r.status === "resolved";
  const nextStatuses = NEXT_STATUSES[r.status];

  const cardClass = [
    styles.card,
    URGENCY_BORDER[r.urgency],
    isResolved ? styles.cardResolved : "",
    highlighted ? styles.cardHighlight : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={cardClass}>
      {/* Header row */}
      <div className={styles.cardHeader}>
        <span className={styles.patientInfo}>
          {r.patient_name} &middot; Room {r.room_number}
        </span>
        <div className={styles.badges}>
          <span className={`${styles.badge} ${URGENCY_BADGE[r.urgency]}`}>
            {URGENCY_LABEL[r.urgency]}
          </span>
          <span className={styles.categoryBadge}>
            {CATEGORY_LABEL[r.category] ?? r.category}
          </span>
        </div>
      </div>

      {/* AI Summary */}
      <p className={styles.summaryText}>{r.summary}</p>

      {/* Original transcript */}
      <p className={styles.transcript}>&ldquo;{r.transcript}&rdquo;</p>

      {/* Suggested action */}
      <p className={styles.suggestedAction}>
        <span className={styles.suggestedActionLabel}>Suggested: </span>
        {r.suggested_action}
      </p>

      {/* Patient context */}
      {r.patient_context && (
        <div className={styles.patientContext}>
          <span className={styles.patientContextLabel}>Patient Context: </span>
          {r.patient_context}
        </div>
      )}

      {/* Timestamp */}
      <div className={styles.timestamp}>{formatTime(r.created_at)}</div>

      {/* Footer: status controls */}
      <div className={styles.cardFooter}>
        <span className={`${styles.currentStatus} ${STATUS_CLASS[r.status]}`}>
          {STATUS_LABEL[r.status]}
        </span>
        {nextStatuses.length > 0 && (
          <div className={styles.statusGroup}>
            {nextStatuses.map((s) => (
              <button
                key={s}
                className={styles.statusBtn}
                onClick={() => onStatusChange(r.request_id, s)}
              >
                Mark {STATUS_LABEL[s]}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
