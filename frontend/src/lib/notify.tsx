import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";
import { AlertTriangle, X } from "lucide-react";
import { apiErrorMessage } from "@/lib/api";

interface Toast {
  id: number;
  title: string;
  message: string;
}

interface NotifyValue {
  /** Shows a failed action where the user can see it, whatever they have scrolled to, until they dismiss it. */
  error: (title: string, error: unknown) => void;
}

const MAX_TOASTS = 3;

const NotifyContext = createContext<NotifyValue | null>(null);

export function NotifyProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(0);

  // An error belongs to the page it happened on.
  useEffect(() => setToasts([]), [pathname]);

  const error = useCallback((title: string, cause: unknown) => {
    const message = apiErrorMessage(cause);
    setToasts((current) => [
      { id: nextId.current++, title, message },
      ...current.filter((toast) => toast.title !== title || toast.message !== message),
    ].slice(0, MAX_TOASTS));
  }, []);
  const dismiss = (id: number) => setToasts((current) => current.filter((toast) => toast.id !== id));
  const value = useMemo<NotifyValue>(() => ({ error }), [error]);

  return (
    <NotifyContext.Provider value={value}>
      {children}
      <div aria-live="assertive" className="pointer-events-none fixed inset-x-4 top-4 z-[90] flex flex-col items-end gap-2 sm:left-auto sm:w-96">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            role="alert"
            className="pointer-events-auto flex w-full items-start gap-2.5 rounded-xl border border-danger-ring bg-white px-3.5 py-3 text-[13px] shadow-lg"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
            <div className="min-w-0 flex-1">
              <p className="font-semibold text-ink">{toast.title}</p>
              <p className="mt-0.5 break-words text-ink-muted">{toast.message}</p>
            </div>
            <button type="button" onClick={() => dismiss(toast.id)} aria-label={t("notify.dismiss")} className="-mr-1 rounded p-0.5 text-ink-subtle hover:text-ink">
              <X className="h-4 w-4" />
            </button>
          </div>
        ))}
      </div>
    </NotifyContext.Provider>
  );
}

export function useNotify(): NotifyValue {
  const value = useContext(NotifyContext);
  if (!value) throw new Error("useNotify must be used inside NotifyProvider");
  return value;
}
