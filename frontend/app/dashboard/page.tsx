"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { listRequests, updateStatus, WS_URL } from "../../lib/api";
import type { CareRequest, Status, Urgency } from "../../lib/types";
import { useAccount } from "../context/AccountContext";
import styles from "./dashboard.module.css";

const URGENCY_RANK: Record<Urgency, number> = { emergency: 0, high: 1, medium: 2, low: 3 };

const URGENCY_LABEL: Record<Urgency, string> = {
  emergency: "Emergency", high: "High", medium: "Medium", low: "Low",
};

const CATEGORY_LABEL: Record<string, string> = {
  urgent_medical:       "Medical",
  in_person_caregiver:  "Caregiver",
  routine_comfort:      "Comfort",
  automated_task:       "Automated",
  family_communication: "Family",
  general_conversation: "General",
};

const STATUS_LABEL: Record<Status, string> = {
  new: "New", in_progress: "In Progress", resolved: "Resolved",
};

const NEXT: Record<Status, Status[]> = {
  new: ["in_progress"], in_progress: ["resolved"], resolved: [],
};

const URGENCY_TAG: Record<Urgency, string> = {
  emergency: styles.tagEmergency, high: styles.tagHigh,
  medium: styles.tagMedium, low: styles.tagLow,
};

const URGENCY_BORDER: Record<Urgency, string> = {
  emergency: styles.urgencyEmergency, high: styles.urgencyHigh,
  medium: styles.urgencyMedium, low: styles.urgencyLow,
};

const STATUS_PILL: Record<Status, string> = {
  new: styles.statusNew, in_progress: styles.statusInProgress, resolved: styles.statusResolved,
};

type Filter = "all" | "emergency" | "high" | "in_progress";

function sort(list: CareRequest[]) {
  return [...list].sort((a, b) => {
    const ar = a.status === "resolved" ? 1 : 0, br = b.status === "resolved" ? 1 : 0;
    if (ar !== br) return ar - br;
    const ud = URGENCY_RANK[a.urgency] - URGENCY_RANK[b.urgency];
    return ud !== 0 ? ud : new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
  });
}

function fmt(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export default function Dashboard() {
  const { account } = useAccount();
  const router      = useRouter();

  const [requests,  setRequests]  = useState<CareRequest[]>([]);
  const [connected, setConnected] = useState(false);
  const [filter,    setFilter]    = useState<Filter>("all");

  const highlighted = useRef<Set<string>>(new Set());
  const knownIds    = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!account) router.replace("/");
  }, [account, router]);

  const refresh = useCallback(async () => {
    try {
      const data = await listRequests();
      setRequests(prev => {
        const prevIds = new Set(prev.map(r => r.request_id));
        for (const r of data) {
          if (!prevIds.has(r.request_id) && !knownIds.current.has(r.request_id)
              && (r.urgency === "emergency" || r.urgency === "high")) {
            highlighted.current.add(r.request_id);
            setTimeout(() => highlighted.current.delete(r.request_id), 4000);
          }
          knownIds.current.add(r.request_id);
        }
        return sort(data);
      });
    } catch {}
  }, []);

  useEffect(() => {
    refresh();
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout>;
    function connect() {
      ws = new WebSocket(WS_URL);
      ws.onopen  = () => setConnected(true);
      ws.onclose = () => { setConnected(false); timer = setTimeout(connect, 3000); };
      ws.onerror = () => ws?.close();
      ws.onmessage = () => refresh();
    }
    connect();
    return () => { clearTimeout(timer); ws?.close(); };
  }, [refresh]);

  async function changeStatus(id: string, s: Status) {
    try { await updateStatus(id, s); await refresh(); } catch {}
  }

  if (!account) return null;

  const active   = requests.filter(r => r.status !== "resolved");
  const resolved = requests.filter(r => r.status === "resolved");

  const statActive    = active.length;
  const statEmergency = requests.filter(r => r.urgency === "emergency" && r.status !== "resolved").length;
  const statInProg    = requests.filter(r => r.status === "in_progress").length;
  const statResolved  = resolved.length;

  const filteredActive = active.filter(r => {
    if (filter === "all")         return true;
    if (filter === "emergency")   return r.urgency === "emergency";
    if (filter === "high")        return r.urgency === "high";
    if (filter === "in_progress") return r.status === "in_progress";
    return true;
  });

  const FILTERS: { key: Filter; label: string }[] = [
    { key: "all",         label: "All" },
    { key: "emergency",   label: "Emergency" },
    { key: "high",        label: "High" },
    { key: "in_progress", label: "In Progress" },
  ];

  return (
    <div className={styles.page}>

      {/* ── Left dark sidebar ── */}
      <div className={styles.sidebar}>
        <div className={styles.sidebarProfile}>
          <div className={styles.sidebarAvatar} style={{ background: account.color }}>
            {account.initials}
          </div>
          <div className={styles.sidebarName}>{account.name.split(" ")[0]}.</div>
          <div className={styles.sidebarRole}>{account.subtitle}</div>
        </div>

        <div className={styles.sidebarDivider} />

        <div className={styles.sidebarSection}>
          <div className={styles.sidebarSectionLabel}>Overview</div>
          <div className={styles.sidebarStats}>
            {[
              { label: "Active",      value: statActive,    alert: false },
              { label: "Emergency",   value: statEmergency, alert: true  },
              { label: "In Progress", value: statInProg,    alert: false },
              { label: "Resolved",    value: statResolved,  alert: false },
            ].map(s => (
              <div key={s.label} className={styles.sidebarStat}>
                <span className={styles.sidebarStatLabel}>{s.label}</span>
                <span className={`${styles.sidebarStatNum} ${s.alert && s.value > 0 ? styles.sidebarStatNumAlert : ""}`}>
                  {s.value}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className={styles.sidebarDivider} />

        <div className={styles.sidebarLive}>
          <div className={styles.sidebarLiveDot} />
          <span className={`${styles.sidebarLiveLabel} ${connected ? styles.sidebarLiveLabelOn : ""}`}>
            {connected ? "Live" : "Connecting…"}
          </span>
        </div>
      </div>

      {/* ── Main content ── */}
      <div className={styles.main}>
        <div className={styles.inner}>

          <div className={styles.header}>
            <div className={styles.heading}>
              Patient Requests
              {connected && (
                <span className={styles.live}>
                  <span className={styles.liveDot} /> Live
                </span>
              )}
            </div>
          </div>

          {/* Stats row */}
          <div className={styles.statsRow}>
            {[
              { label: "Active",      value: statActive,    alert: false },
              { label: "Emergency",   value: statEmergency, alert: true  },
              { label: "In Progress", value: statInProg,    alert: false },
              { label: "Resolved",    value: statResolved,  alert: false },
            ].map(s => (
              <div key={s.label} className={styles.statCard}>
                <div className={`${styles.statNum} ${s.alert && s.value > 0 ? styles.statNumAlert : ""}`}>
                  {s.value}
                </div>
                <div className={styles.statLabel}>{s.label}</div>
              </div>
            ))}
          </div>

          {/* Filter row */}
          <div className={styles.filterRow}>
            {FILTERS.map(f => (
              <button
                key={f.key}
                className={`${styles.filterTab} ${filter === f.key ? styles.filterTabActive : ""}`}
                onClick={() => setFilter(f.key)}
              >
                {f.label}
              </button>
            ))}
          </div>

          {requests.length === 0 && (
            <div className={styles.empty}>No requests yet — waiting for patients.</div>
          )}

          {filteredActive.length > 0 && (<>
            <div className={styles.section}>Active</div>
            <div className={styles.list}>
              {filteredActive.map(r => (
                <Card key={r.request_id} r={r} highlight={highlighted.current.has(r.request_id)} onChange={changeStatus} />
              ))}
            </div>
          </>)}

          {filter === "all" && resolved.length > 0 && (<>
            <div className={styles.section}>Resolved</div>
            <div className={styles.list}>
              {resolved.map(r => (
                <Card key={r.request_id} r={r} highlight={false} onChange={changeStatus} />
              ))}
            </div>
          </>)}

        </div>
      </div>

    </div>
  );
}

function Card({ r, highlight, onChange }: {
  r: CareRequest;
  highlight: boolean;
  onChange: (id: string, s: Status) => void;
}) {
  const cls = [
    styles.card,
    URGENCY_BORDER[r.urgency],
    r.status === "resolved" ? styles.cardResolved : "",
    highlight ? styles.cardHighlight : "",
  ].filter(Boolean).join(" ");

  return (
    <div className={cls}>
      <div className={styles.cardTop}>
        <span>
          <span className={styles.patientName}>{r.patient_name}</span>
          <span className={styles.room}> · Rm {r.room_number}</span>
        </span>
        <div className={styles.tags}>
          <span className={`${styles.tag} ${URGENCY_TAG[r.urgency]}`}>{URGENCY_LABEL[r.urgency]}</span>
          <span className={styles.tagCategory}>{CATEGORY_LABEL[r.category] ?? r.category}</span>
        </div>
      </div>

      <p className={styles.summary}>{r.summary}</p>
      <p className={styles.quote}>&ldquo;{r.transcript}&rdquo;</p>
      <p className={styles.action}>→ {r.suggested_action}</p>

      {r.patient_context && (
        <div className={styles.context}>{r.patient_context}</div>
      )}

      <div className={styles.cardBottom}>
        <span className={styles.timestamp}>{fmt(r.created_at)}</span>
        <div className={styles.cardFooter}>
          <span className={`${styles.statusPill} ${STATUS_PILL[r.status]}`}>
            {STATUS_LABEL[r.status]}
          </span>
          {NEXT[r.status].length > 0 && (
            <div className={styles.statusActions}>
              {NEXT[r.status].map(s => (
                <button key={s} className={styles.statusBtn} onClick={() => onChange(r.request_id, s)}>
                  {STATUS_LABEL[s]}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
