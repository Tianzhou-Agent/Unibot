import { useTranslation } from "react-i18next";
import { useEffect, useState } from "react";
import { Search } from "lucide-react";
import { AppIcon } from "@/components/apps/PluginUi";
import { api, apiErrorMessage } from "@/lib/api";
import { useMockSession } from "@/lib/mockSession";
import type { AinaInstallation, AinaRecord } from "@/types";

/**
 * The content of a canvas "new tab", like a browser's new-tab page: every app the user can open,
 * this conversation's apps first. Picking one turns the tab into that app.
 */
export function AinaLauncher({ conversationAinaIds, openAinaIds, onSelect }: {
  conversationAinaIds: readonly string[];
  openAinaIds: readonly string[];
  onSelect: (ainaId: string) => void;
}) {
  const { t } = useTranslation("canvas");
  const { profile } = useMockSession();
  const [ainas, setAinas] = useState<AinaRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let active = true;
    const actor = new URLSearchParams({ user_id: profile.actorUserId, tenant_id: profile.tenantId });
    Promise.all([
      api.get<AinaRecord[]>("/ainas"),
      api.get<AinaInstallation[]>(`/installations?${actor}`),
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
  }, [profile.actorUserId, profile.tenantId]);

  const needle = query.trim().toLowerCase();
  const matching = (ainas ?? []).filter(({ manifest }) => !needle
    || [manifest.aina.name, manifest.aina.description, manifest.aina.id].some((field) => field.toLowerCase().includes(needle)));
  const used = matching.filter((record) => conversationAinaIds.includes(record.manifest.aina.id));
  const others = matching.filter((record) => !conversationAinaIds.includes(record.manifest.aina.id));

  function section(label: string, records: AinaRecord[]) {
    if (!records.length) return null;
    return (
      <div role="group" aria-label={label}>
        <p className="mb-1 px-2 text-[12px] font-medium text-ink-subtle">{label}</p>
        {records.map((record) => {
          const { id, name, description } = record.manifest.aina;
          const open = openAinaIds.includes(id);
          return (
            <button
              key={id}
              type="button"
              onClick={() => onSelect(id)}
              className="flex w-full items-center gap-3 rounded-xl px-2 py-2.5 text-left transition-colors hover:bg-app-soft"
            >
              <AppIcon id={id} name={name} />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2">
                  <span className="truncate text-[14px] font-medium text-ink">{name}</span>
                  {open ? <span className="shrink-0 rounded-full bg-app-soft px-2 py-0.5 text-[10.5px] text-ink-muted">{t("launcher.alreadyOpen")}</span> : null}
                </span>
                <span className="mt-0.5 block truncate text-[12.5px] text-ink-muted">{description}</span>
              </span>
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-xl px-6 py-10">
      <h2 className="text-[20px] font-semibold text-ink">{t("launcher.title")}</h2>
      <label className="relative mt-4 block">
        <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-subtle" />
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t("launcher.search")}
          aria-label={t("launcher.search")}
          autoFocus
          className="h-10 w-full rounded-full border border-line bg-white pl-10 pr-4 text-[13.5px] text-ink outline-none transition placeholder:text-ink-subtle focus:border-accent"
        />
      </label>
      <div className="mt-6 space-y-6">
        {error ? <p className="rounded-lg bg-danger-soft px-3 py-2 text-[12.5px] text-danger-deep">{error}</p> : null}
        {!error && !ainas ? <p className="px-2 text-[13px] text-ink-muted">{t("launcher.loading")}</p> : null}
        {ainas && !ainas.length ? <p className="px-2 text-[13px] text-ink-muted">{t("launcher.empty")}</p> : null}
        {ainas && ainas.length > 0 && !matching.length ?<p className="px-2 text-[13px] text-ink-muted">{t("launcher.noMatches", { query })}</p> : null}
        {section(t("launcher.inConversation"), used)}
        {section(t("launcher.allApps"), others)}
      </div>
    </div>
  );
}
