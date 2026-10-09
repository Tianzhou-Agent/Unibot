import { useTranslation } from "react-i18next";
import { useEffect, useRef, useState } from "react";
import { AppWindow, Plus } from "lucide-react";
import { api, apiErrorMessage } from "@/lib/api";
import { useMockSession } from "@/lib/mockSession";
import type { AinaInstallation, AinaRecord } from "@/types";

/** Lets the user open another AINA as a tab: apps this conversation used first, then every other openable app. */
export function AinaSwitcher({ conversationAinaIds, openAinaIds, onSelect }: {
  conversationAinaIds: readonly string[];
  openAinaIds: readonly string[];
  onSelect: (ainaId: string) => void;
}) {
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

  const closed = (ainas ?? []).filter((record) => !openAinaIds.includes(record.manifest.aina.id));
  const used = closed.filter((record) => conversationAinaIds.includes(record.manifest.aina.id));
  const others = closed.filter((record) => !conversationAinaIds.includes(record.manifest.aina.id));

  function section(label: string, records: AinaRecord[]) {
    if (!records.length) return null;
    return (
      <div role="group" aria-label={label} className="py-1">
        <p className="px-3 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-wide text-ink-subtle">{label}</p>
        {records.map((record) => {
          const { id, name, description } = record.manifest.aina;
          return (
            <button
              key={id}
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onSelect(id);
              }}
              className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-app-soft"
            >
              <AppWindow className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[12px] font-semibold text-ink">{name}</span>
                <span className="block truncate text-[10.5px] text-ink-muted">{description}</span>
              </span>
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative mb-1 shrink-0">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t("switcher.open")}
        title={t("switcher.open")}
        className="flex h-7 w-7 items-center justify-center rounded-md text-ink-muted hover:bg-white hover:text-ink"
      >
        <Plus className="h-3.5 w-3.5" />
      </button>
      {open ? (
        <div
          role="menu"
          aria-label={t("switcher.open")}
          className="absolute right-0 top-full z-40 mt-1 w-72 overflow-hidden rounded-lg border border-line bg-white shadow-soft"
        >
          <div className="max-h-80 overflow-y-auto">
            {error ? <p className="m-2 rounded bg-danger-soft px-2 py-1.5 text-[10.5px] text-danger-deep">{error}</p> : null}
            {!error && !ainas ? <p className="px-3 py-2 text-[11.5px] text-ink-muted">{t("switcher.loading")}</p> : null}
            {ainas && !closed.length ? <p className="px-3 py-2 text-[11.5px] text-ink-muted">{t("switcher.empty")}</p> : null}
            {section(t("switcher.inConversation"), used)}
            {section(t("switcher.allApps"), others)}
          </div>
        </div>
      ) : null}
    </div>
  );
}
