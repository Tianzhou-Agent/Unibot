import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

const STORAGE_KEY = "unibot:debug-mode";

interface DebugModeValue {
  debugMode: boolean;
  setDebugMode: (enabled: boolean) => void;
}

const DebugModeContext = createContext<DebugModeValue | null>(null);

export function DebugModeProvider({ children }: { children: ReactNode }) {
  const [debugMode, setDebugModeState] = useState(() => window.localStorage.getItem(STORAGE_KEY) === "true");

  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY) setDebugModeState(event.newValue === "true");
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);

  const value = useMemo<DebugModeValue>(() => ({
    debugMode,
    setDebugMode: (enabled) => {
      window.localStorage.setItem(STORAGE_KEY, String(enabled));
      setDebugModeState(enabled);
    },
  }), [debugMode]);

  return <DebugModeContext.Provider value={value}>{children}</DebugModeContext.Provider>;
}

export function useDebugMode(): DebugModeValue {
  const value = useContext(DebugModeContext);
  if (!value) throw new Error("useDebugMode must be used inside DebugModeProvider");
  return value;
}
