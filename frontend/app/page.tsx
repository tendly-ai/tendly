"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAccount, ACCOUNTS, type Account } from "./context/AccountContext";
import styles from "./login.module.css";

const DEMO_PASSWORD = "1234";

export default function LoginPage() {
  const { setAccount } = useAccount();
  const router = useRouter();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [password, setPassword]     = useState("");
  const [pwError, setPwError]       = useState(false);
  const [addToast, setAddToast]     = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function selectAccount(a: Account) {
    // Patients go straight through — no password
    if (a.role === "patient") {
      setAccount(a);
      router.push("/patient");
      return;
    }
    // Caregivers expand the password field
    if (selectedId === a.id) { setSelectedId(null); setPassword(""); setPwError(false); return; }
    setSelectedId(a.id);
    setPassword("");
    setPwError(false);
    setTimeout(() => inputRef.current?.focus(), 50);
  }

  function handleContinue(a: Account) {
    if (password !== DEMO_PASSWORD) { setPwError(true); return; }
    setAccount(a);
    router.push("/dashboard");
  }

  function handleKeyDown(e: React.KeyboardEvent, a: Account) {
    if (e.key === "Enter") handleContinue(a);
  }

  function handleAddAccount() {
    setAddToast(true);
    setTimeout(() => setAddToast(false), 2000);
  }

  const patients   = ACCOUNTS.filter(a => a.role === "patient");
  const caregivers = ACCOUNTS.filter(a => a.role === "caregiver");

  function renderRow(a: Account) {
    const isSelected = selectedId === a.id;
    const isCg = a.role === "caregiver";
    return (
      <div key={a.id}>
        <button
          className={`${styles.accountRow} ${isSelected ? styles.accountRowSelected : ""}`}
          onClick={() => selectAccount(a)}
        >
          <div className={styles.avatar} style={{ background: a.color }}>{a.initials}</div>
          <div className={styles.accountInfo}>
            <div className={styles.accountName}>{a.name}</div>
            <div className={styles.accountSub}>{a.subtitle}</div>
          </div>
          <div
            className={styles.chevron}
            style={{ opacity: isSelected ? 1 : 0, transform: isSelected ? "rotate(90deg)" : "none" }}
          >›</div>
        </button>

        {isCg && isSelected && (
          <div className={styles.passwordRow}>
            <input
              ref={inputRef}
              type="password"
              className={`${styles.passwordInput} ${pwError ? styles.passwordInputError : ""}`}
              placeholder="Password"
              value={password}
              onChange={e => { setPassword(e.target.value); setPwError(false); }}
              onKeyDown={e => handleKeyDown(e, a)}
              autoComplete="current-password"
            />
            {pwError && <p className={styles.pwError}>Incorrect password. Try 1234.</p>}
            <button className={styles.continueBtn} onClick={() => handleContinue(a)}>
              Continue →
            </button>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className={styles.root}>
      {/* Left panel */}
      <div className={styles.left}>
        <div className={styles.leftContent}>
          <div className={styles.logoRow}>
            <span className={styles.logoMark}>✦</span>
            <span className={styles.logoText}>Tendly</span>
          </div>
          <div className={styles.tagline}>Care that&apos;s<br />always there.</div>
          <p className={styles.tagSub}>Connecting patients and caregivers through AI.</p>
        </div>
        <div className={styles.leftFooter}>Hackathon MVP — CARE-001</div>
      </div>

      {/* Right panel */}
      <div className={styles.right}>
        <div className={styles.rightInner}>
          <h1 className={styles.welcome}>Welcome back.</h1>
          <p className={styles.welcomeSub}>Choose your profile to continue.</p>

          <div className={styles.section}>
            <div className={styles.sectionLabel}>Patients</div>
            <div className={styles.accountList}>{patients.map(renderRow)}</div>
          </div>

          <div className={styles.divider} />

          <div className={styles.section}>
            <div className={styles.sectionLabel}>Care Team</div>
            <div className={styles.accountList}>{caregivers.map(renderRow)}</div>
          </div>

          <div className={styles.addAccountRow}>
            <button className={styles.addAccountBtn} onClick={handleAddAccount}>
              <span className={styles.addIcon}>+</span> Add account
            </button>
            {addToast && <span className={styles.toast}>Coming soon</span>}
          </div>
        </div>
      </div>
    </div>
  );
}
