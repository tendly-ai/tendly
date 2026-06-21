"use client";

import { createContext, useContext, useEffect, useState } from "react";

export type Role = "patient" | "caregiver";

export interface Account {
  id: string;
  name: string;
  role: Role;
  initials: string;
  subtitle: string;
  color: string;
}

export const ACCOUNTS: Account[] = [
  { id: "patient_001", name: "Mary Johnson",  role: "patient",   initials: "MJ", subtitle: "Patient · Room 204B", color: "#7c3aed" },
  { id: "patient_002", name: "Robert Chen",   role: "patient",   initials: "RC", subtitle: "Patient · Room 108A", color: "#7c3aed" },
  { id: "patient_003", name: "Gloria Reyes",  role: "patient",   initials: "GR", subtitle: "Patient · Room 312C", color: "#7c3aed" },
  { id: "nurse_jenny", name: "Jenny Park",    role: "caregiver", initials: "JP", subtitle: "RN · Charge Nurse",   color: "#0284c7" },
  { id: "aide_carlos", name: "Carlos Mendez", role: "caregiver", initials: "CM", subtitle: "Care Aide",           color: "#0284c7" },
];

interface AccountCtx {
  account: Account | null;
  setAccount: (a: Account | null) => void;
}

const Ctx = createContext<AccountCtx>({ account: null, setAccount: () => {} });

export function AccountProvider({ children }: { children: React.ReactNode }) {
  const [account, setAccountState] = useState<Account | null>(null);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    try {
      const stored = localStorage.getItem("cl_account");
      if (stored) {
        const found = ACCOUNTS.find(a => a.id === stored);
        if (found) setAccountState(found);
      }
    } catch {}
    setHydrated(true);
  }, []);

  function setAccount(a: Account | null) {
    setAccountState(a);
    try {
      if (a) localStorage.setItem("cl_account", a.id);
      else localStorage.removeItem("cl_account");
    } catch {}
  }

  if (!hydrated) return null;

  return <Ctx.Provider value={{ account, setAccount }}>{children}</Ctx.Provider>;
}

export function useAccount() {
  return useContext(Ctx);
}
