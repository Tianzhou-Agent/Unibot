import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { AUTH_REQUIRED_EVENT, ApiError, api } from "@/lib/api";

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  avatar_url: string | null;
  tenant_id: string;
  providers: Array<"password" | "github">;
  is_admin: boolean;
}

interface AuthConfig {
  auth_required: boolean;
  registration_enabled: boolean;
  github_enabled: boolean;
}

interface AuthResponse {
  user: AuthUser;
}

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  config: AuthConfig;
  login: (email: string, password: string) => Promise<void>;
  register: (name: string, email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const DEFAULT_CONFIG: AuthConfig = {
  auth_required: false,
  registration_enabled: true,
  github_enabled: false,
};

const LEGACY_USER: AuthUser = {
  id: "anonymous",
  email: "local@unibot.invalid",
  name: "本地用户",
  avatar_url: null,
  tenant_id: "default",
  providers: [],
  is_admin: false,
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [config, setConfig] = useState<AuthConfig>(DEFAULT_CONFIG);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    async function initialize() {
      try {
        const nextConfig = await api.get<AuthConfig>("/auth/config");
        if (!active) return;
        setConfig(nextConfig);
        if (!nextConfig.auth_required) {
          setUser(LEGACY_USER);
          return;
        }
        try {
          const response = await api.get<AuthResponse>("/auth/me");
          if (active) setUser(response.user);
        } catch (error) {
          if (!(error instanceof ApiError) || error.status !== 401) throw error;
          if (active) setUser(null);
        }
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) {
          if (active) setUser(LEGACY_USER);
        } else if (active) {
          setUser(null);
          setConfig({ ...DEFAULT_CONFIG, auth_required: true });
        }
      } finally {
        if (active) setLoading(false);
      }
    }
    void initialize();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!config.auth_required) return;
    const clearExpiredSession = () => setUser(null);
    window.addEventListener(AUTH_REQUIRED_EVENT, clearExpiredSession);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, clearExpiredSession);
  }, [config.auth_required]);

  const value = useMemo<AuthContextValue>(() => ({
    user,
    loading,
    config,
    login: async (email, password) => {
      const response = await api.post<AuthResponse>("/auth/login", { email, password });
      setUser(response.user);
    },
    register: async (name, email, password) => {
      const response = await api.post<AuthResponse>("/auth/register", { name, email, password });
      setUser(response.user);
    },
    logout: async () => {
      await api.post<void>("/auth/logout");
      setUser(null);
    },
  }), [config, loading, user]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-app-bg text-sm text-ink-muted">
        正在检查登录状态…
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return children;
}
