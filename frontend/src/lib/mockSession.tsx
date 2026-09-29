import i18n from "@/i18n";
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
    get name() { return i18n.t("common:mock.userName"); },
    role: "user",
    get roleLabel() { return i18n.t("common:mock.role.user"); },
    get tenant() { return i18n.t("common:mock.tenant"); },
    tenantId: "default",
    actorUserId: "anonymous",
    get initials() { return i18n.t("common:mock.userInitials"); },
  },
  admin: {
    id: "admin-zhou-ran",
    get name() { return i18n.t("common:mock.adminName"); },
    role: "admin",
    get roleLabel() { return i18n.t("common:mock.role.admin"); },
    get tenant() { return i18n.t("common:mock.tenant"); },
    tenantId: "default",
    actorUserId: "admin-zhou-ran",
    get initials() { return i18n.t("common:mock.adminInitials"); },
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
