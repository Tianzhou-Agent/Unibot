import { useTranslation } from "react-i18next";
import { useEffect, useRef, useState } from "react";
import { AppWindow, Check, ChevronDown } from "lucide-react";
import { api, apiErrorMessage } from "@/lib/api";
import { useMockSession } from "@/lib/mockSession";
import { classNames } from "@/lib/utils";
import type { AinaInstallation, AinaRecord } from "@/types";

/** Lists the AINAs the actor can open and attaches the chosen one to the current conversation. */
export function AinaPicker({ activeAinaId, onSelect }: { activeAinaId: string | null; onSelect: (ainaId: string) => void }) {
  const { t } = useTranslation("canvas");
  const { profile } = useMockSession();
  const [open, setOpen] = useState(false);
  const [ainas, setAinas] = useState<AinaRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    let active = true;
    const query = new URLSearchParams({ user_id: profile.actorUserId, tenant_id: profile.tenantId });
    Promise.all([
      api.get<AinaRecord[]>("/ainas"),
      api.get<AinaInstallation[]>(`/installations?${query}`),
    ])
      .then(([records, installations]) => {
        if (!active) return;
        // Mirrors the backend's openable check: builtin, or installed with every permission granted.
        const installed = new Map(installations.filter((item) => item.status === "active").map((item) => [item.aina_id, item]));
        setAinas(records.filter((record) => {
          if (record.status !== "registered") return false;
          if (record.manifest.runtime.type === "builtin") return true;
          const installation = installed.get(record.manifest.aina.id);
          return Boolean(installation) && record.manifest.permissions.every((permission) => installation?.granted_permissions.includes(permission));
        }));
        setError(null);
      })
      .catch((loadError) => active && setError(apiErrorMessage(loadError)));
    return () => {
      active = false;
    };
  }, [open, profile.actorUserId, profile.tenantId]);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) setOpen(false);
    }
    function onEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onEscape);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onEscape);
    };
  }, [open]);

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={t("openApp")}
        className="btn-outline h-8"
      >
        <AppWindow className="h-3.5 w-3.5" /><span className="hidden sm:inline">{t("openApp")}</span>
        <ChevronDown className={classNames("h-3 w-3 transition-transform", open && "rotate-180")} />
      </button>
      {open ? (
        <div
          role="listbox"
          aria-label={t("chooseApp")}
          className="absolute right-0 top-full z-40 mt-1.5 w-72 overflow-hidden rounded-lg border border-line bg-white shadow-card"
        >
          <div className="max-h-80 overflow-y-auto py-1">
            {error ? <p className="mx-2 my-1 rounded bg-danger-soft px-2 py-1.5 text-[10.5px] text-danger-deep">{error}</p> : null}
            {!error && !ainas ? <p className="px-3 py-2 text-[11.5px] text-ink-muted">{t("loadingApps")}</p> : null}
            {ainas && !ainas.length ? <p className="px-3 py-2 text-[11.5px] text-ink-muted">{t("noApps")}</p> : null}
            {ainas?.map((record) => {
              const { id, name, description } = record.manifest.aina;
              const selected = id === activeAinaId;
              return (
                <button
                  key={id}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  onClick={() => {
                    setOpen(false);
                    onSelect(id);
                  }}
                  className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-app-soft"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[12px] font-semibold text-ink">{name}</span>
                    <span className="block truncate text-[10.5px] text-ink-muted">{description}</span>
                  </span>
                  {selected ? <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" /> : null}
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}
