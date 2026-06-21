"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import { useAccount } from "../context/AccountContext";

const PAGE_TITLES: Record<string, string> = {
  "/patient":   "Patient Portal",
  "/dashboard": "Care Dashboard",
};

export default function Nav() {
  const { account, setAccount } = useAccount();
  const router   = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const dropRef = useRef<HTMLDivElement>(null);

  const pageTitle = PAGE_TITLES[pathname] ?? "";

  useEffect(() => {
    function handler(e: MouseEvent) {
      if (dropRef.current && !dropRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    if (open) document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  return (
    <header style={{
      position: "sticky",
      top: 0,
      zIndex: 50,
      height: 52,
      background: "rgba(255,255,255,0.88)",
      backdropFilter: "blur(12px)",
      WebkitBackdropFilter: "blur(12px)",
      borderBottom: "1px solid #e4e4e7",
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      padding: "0 24px",
    }}>
      {/* Left: logo + page title */}
      <div style={{ display: "flex", alignItems: "center", gap: 0 }}>
        <Link href="/" style={{
          fontSize: 14,
          fontWeight: 600,
          letterSpacing: "-0.3px",
          color: "#09090b",
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}>
          <span style={{ color: "#7c3aed", fontSize: 12 }}>✦</span>
          Tendly
        </Link>
        {pageTitle && (
          <>
            <span style={{ color: "#d4d4d8", margin: "0 10px", fontSize: 14 }}>/</span>
            <span style={{ fontSize: 14, fontWeight: 500, color: "#71717a", letterSpacing: "-0.1px" }}>
              {pageTitle}
            </span>
          </>
        )}
      </div>

      {/* Right: avatar + dropdown */}
      {account && (
        <div ref={dropRef} style={{ position: "relative" }}>
          <button
            onClick={() => setOpen(o => !o)}
            style={{
              width: 32,
              height: 32,
              borderRadius: "50%",
              background: account.color,
              border: open ? "2px solid #09090b" : "2px solid transparent",
              color: "#fff",
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: "0.3px",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              transition: "border-color 0.12s",
            }}
          >
            {account.initials}
          </button>

          {open && (
            <div style={{
              position: "absolute",
              top: 40,
              right: 0,
              background: "#ffffff",
              border: "1px solid #e4e4e7",
              borderRadius: 12,
              boxShadow: "0 4px 24px rgba(0,0,0,0.10), 0 1px 4px rgba(0,0,0,0.06)",
              padding: "12px",
              minWidth: 210,
              zIndex: 100,
            }}>
              {/* Account info */}
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
                <div style={{
                  width: 36,
                  height: 36,
                  borderRadius: "50%",
                  background: account.color,
                  color: "#fff",
                  fontSize: 12,
                  fontWeight: 700,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                }}>
                  {account.initials}
                </div>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: "#09090b" }}>{account.name}</div>
                  <div style={{ fontSize: 11, color: "#71717a", marginTop: 1 }}>{account.subtitle}</div>
                </div>
              </div>

              <div style={{ height: 1, background: "#f4f4f5", margin: "0 0 10px" }} />

              <button
                onClick={() => { setOpen(false); setAccount(null); router.push("/"); }}
                style={{
                  width: "100%",
                  padding: "7px 10px",
                  textAlign: "left",
                  fontSize: 13,
                  fontWeight: 500,
                  color: "#52525b",
                  background: "none",
                  border: "none",
                  borderRadius: 7,
                  cursor: "pointer",
                  transition: "background 0.1s, color 0.1s",
                }}
                onMouseEnter={e => { (e.target as HTMLButtonElement).style.background = "#f4f4f5"; (e.target as HTMLButtonElement).style.color = "#09090b"; }}
                onMouseLeave={e => { (e.target as HTMLButtonElement).style.background = "none"; (e.target as HTMLButtonElement).style.color = "#52525b"; }}
              >
                ↩ Switch profile
              </button>
            </div>
          )}
        </div>
      )}
    </header>
  );
}
