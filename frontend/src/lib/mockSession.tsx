import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

export type MockRole = "user" | "admin";

export interface MockProfile {
  id: string;
  name: string;
  role: MockRole;
  roleLabel: string;
  tenant: string;
  tenantId: string;
  actorUserId: string;
  initials: string;
}

const MOCK_PROFILES: Record<MockRole, MockProfile> = {
  user: {
    id: "user-lin-chen",
    name: "林晨",
    role: "user",
    roleLabel: "普通用户",
    tenant: "天舟科技",
    tenantId: "default",
    actorUserId: "anonymous",
    initials: "林",
  },
  admin: {
    id: "admin-zhou-ran",
    name: "周然",
    role: "admin",
    roleLabel: "平台管理员",
    tenant: "天舟科技",
    tenantId: "default",
    actorUserId: "admin-zhou-ran",
    initials: "周",
  },
};

interface MockSessionValue {
  profile: MockProfile;
  isAdmin: boolean;
  setRole: (role: MockRole) => void;
  toggleRole: () => void;
}

const MockSessionContext = createContext<MockSessionValue | null>(null);
const STORAGE_KEY = "unibot:mock-role";

export function MockSessionProvider({ children }: { children: ReactNode }) {
  const [role, setRoleState] = useState<MockRole>(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored === "admin" ? "admin" : "user";
  });

  function setRole(nextRole: MockRole) {
    window.localStorage.setItem(STORAGE_KEY, nextRole);
    setRoleState(nextRole);
  }

  const value = useMemo<MockSessionValue>(() => ({
    profile: MOCK_PROFILES[role],
    isAdmin: role === "admin",
    setRole,
    toggleRole: () => setRole(role === "admin" ? "user" : "admin"),
  }), [role]);

  return <MockSessionContext.Provider value={value}>{children}</MockSessionContext.Provider>;
}

export function useMockSession(): MockSessionValue {
  const value = useContext(MockSessionContext);
  if (!value) throw new Error("useMockSession must be used inside MockSessionProvider");
  return value;
}
