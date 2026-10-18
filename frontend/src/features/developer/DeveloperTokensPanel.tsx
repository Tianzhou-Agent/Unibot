import { useTranslation } from "react-i18next";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { Check, Copy, KeyRound, Loader2, Plus, Trash2 } from "lucide-react";
import { api, apiErrorMessage, apiUrl } from "@/lib/api";
import { currentLocale } from "@/i18n";
import type { DeveloperToken } from "@/types";

const EXPIRY_DAYS = [30, 90, 365];

/** Settings → Developer: tokens for `aina publish`, created here and shown once. */
export function DeveloperTokensPanel() {
  const { t } = useTranslation("settings");
  const [tokens, setTokens] = useState<DeveloperToken[] | null>(null);
  const [name, setName] = useState("");
  const [days, setDays] = useState(90);
  const [created, setCreated] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hostUrl = new URL(apiUrl(""), window.location.origin).toString().replace(/\/$/, "");

  const load = useCallback(async () => {
    try {
      setTokens(await api.get<DeveloperToken[]>("/developer-tokens"));
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ token: string }>("/developer-tokens", { name: name.trim(), expires_in_days: days });
      setCreated(result.token);
      setCopied(false);
      setName("");
      await load();
    } catch (createError) {
      setError(apiErrorMessage(createError));
    } finally {
      setBusy(false);
    }
  }

  async function revoke(token: DeveloperToken) {
    if (!window.confirm(t("developer.confirmRevoke", { name: token.name }))) return;
    try {
      await api.delete(`/developer-tokens/${encodeURIComponent(token.id)}`);
      await load();
    } catch (revokeError) {
      setError(apiErrorMessage(revokeError));
    }
  }

  async function copy() {
    if (!created) return;
    await navigator.clipboard.writeText(created);
    setCopied(true);
  }

  const date = (iso: string) => new Date(iso).toLocaleDateString(currentLocale());

  return (
    <>
      <div className="mb-4">
        <h2 className="text-[16px] font-extrabold text-ink">{t("developer.title")}</h2>
        <p className="mt-0.5 text-[11.5px] text-ink-muted">{t("developer.desc")}</p>
      </div>
      {error ? <div className="mb-4 rounded-lg border border-danger-ring bg-danger-soft px-3 py-2 text-[12px] text-danger-deep" role="alert">{error}</div> : null}

      <section className="mb-4 rounded-xl border border-line bg-white p-4 shadow-soft">
        <h3 className="text-[13px] font-extrabold text-ink">{t("developer.tokens")}</h3>
        <p className="mt-0.5 text-[11.5px] leading-5 text-ink-muted">{t("developer.tokensDesc")}</p>
        <form onSubmit={(event) => void create(event)} className="mt-3 flex flex-wrap items-end gap-2">
          <label className="min-w-[180px] flex-1">
            <span className="mb-1 block text-[11px] font-bold text-ink-muted">{t("developer.name")}</span>
            <input required maxLength={80} value={name} onChange={(event) => setName(event.target.value)} className="input-soft" placeholder={t("developer.namePlaceholder")} />
          </label>
          <label>
            <span className="mb-1 block text-[11px] font-bold text-ink-muted">{t("developer.expires")}</span>
            <select value={days} onChange={(event) => setDays(Number(event.target.value))} className="input-soft">
              {EXPIRY_DAYS.map((value) => <option key={value} value={value}>{t("developer.days", { count: value })}</option>)}
            </select>
          </label>
          <button type="submit" disabled={busy || !name.trim()} className="btn-primary h-9 text-[11.5px]">
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}{t("developer.create")}
          </button>
        </form>

        {created ? (
          <div className="mt-3 rounded-lg border border-warning-ring bg-warning-soft p-3" role="status">
            <p className="text-[11.5px] font-bold text-warning-deep">{t("developer.copyNow")}</p>
            <div className="mt-2 flex items-center gap-2">
              <code className="min-w-0 flex-1 break-all rounded bg-white px-2 py-1.5 font-mono text-[11.5px] text-ink" aria-label={t("developer.newToken")}>{created}</code>
              <button type="button" onClick={() => void copy()} className="btn-outline h-8 text-[11.5px]">
                {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}{copied ? t("developer.copied") : t("developer.copy")}
              </button>
            </div>
          </div>
        ) : null}

        <ul className="mt-3 divide-y divide-line">
          {tokens === null ? <li className="py-2 text-[11.5px] text-ink-muted">{t("developer.loading")}</li> : null}
          {tokens?.length === 0 ? <li className="py-2 text-[11.5px] text-ink-muted">{t("developer.empty")}</li> : null}
          {tokens?.map((token) => (
            <li key={token.id} className="flex flex-wrap items-center gap-3 py-2.5">
              <KeyRound className="h-4 w-4 shrink-0 text-ink-subtle" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-[12.5px] font-bold text-ink">{token.name} <span className="font-mono text-[11px] font-normal text-ink-subtle">{token.prefix}…</span></p>
                <p className="text-[10.5px] text-ink-muted">
                  {t("developer.meta", { created: date(token.created_at), expires: date(token.expires_at) })}
                  {" · "}{token.last_used_at ? t("developer.lastUsed", { date: date(token.last_used_at) }) : t("developer.neverUsed")}
                </p>
              </div>
              <button type="button" onClick={() => void revoke(token)} className="btn-outline h-8 text-[11.5px]" aria-label={t("developer.revokeNamed", { name: token.name })}>
                <Trash2 className="h-3.5 w-3.5" />{t("developer.revoke")}
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="mb-4 rounded-xl border border-line bg-white p-4 shadow-soft">
        <h3 className="text-[13px] font-extrabold text-ink">{t("developer.cli")}</h3>
        <p className="mt-0.5 text-[11.5px] leading-5 text-ink-muted">{t("developer.cliDesc")}</p>
        <pre className="mt-3 overflow-x-auto rounded-lg bg-app-inverse p-3 font-mono text-[11.5px] leading-5 text-ink-inverse">{[
          'aina init notes --id com.example.notes --name "Notes"',
          "python notes/server/main.py",
          "aina validate notes/package --live",
          `aina publish notes/package --host ${hostUrl} --token <token>`,
        ].join("\n")}</pre>
      </section>
    </>
  );
}
