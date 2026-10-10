import { useTranslation } from "react-i18next";
import { useCallback, useEffect, useState } from "react";
import { AppWindow, ChevronRight, Download, Trash2, Unplug } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AinaCapabilitySections } from "@/components/apps/AinaCapabilities";
import { ActionMenu, AppIcon, type MenuItem } from "@/components/apps/PluginUi";
import { Topbar } from "@/components/layout/Topbar";
import { api, apiErrorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ACTOR_QUERY, deleteAina, installAina, openAina, uninstallAina } from "@/lib/plugins";
import { classNames } from "@/lib/utils";
import type { AinaInstallation, AinaRecord } from "@/types";

export default function PluginDetailPage() {
  const { ainaId = "" } = useParams();
  const { t } = useTranslation("apps");
  const { t: tAina } = useTranslation("aina");
  const navigate = useNavigate();
  const { user, config } = useAuth();
  const canManageRegistry = !config.auth_required || Boolean(user?.is_admin);
  const [record, setRecord] = useState<AinaRecord | null>(null);
  const [installed, setInstalled] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [ainas, installations] = await Promise.all([
        api.get<AinaRecord[]>("/ainas"),
        api.get<AinaInstallation[]>(`/installations?${ACTOR_QUERY}`),
      ]);
      setRecord(ainas.find((item) => item.manifest.aina.id === ainaId) ?? null);
      setInstalled(installations.some((item) => item.aina_id === ainaId && item.status === "active"));
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [ainaId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(action: () => Promise<unknown>, after?: () => void) {
    setBusy(true);
    setError(null);
    try {
      await action();
      if (after) after();
      else await load();
    } catch (actionError) {
      setError(apiErrorMessage(actionError));
    } finally {
      setBusy(false);
    }
  }

  const manifest = record?.manifest;
  const builtin = manifest?.runtime.type === "builtin";
  const usable = builtin || installed;
  const menuItems: MenuItem[] = [];
  if (manifest && installed && !builtin) {
    menuItems.push({ label: t("aina.uninstall"), icon: <Unplug className="h-4 w-4" />, onSelect: () => void run(() => uninstallAina(ainaId)) });
  }
  if (manifest && canManageRegistry && !builtin && manifest.runtime.type !== "managed") {
    menuItems.push({ label: t("delete"), icon: <Trash2 className="h-4 w-4" />, danger: true, onSelect: () => void run(() => deleteAina(ainaId), () => navigate("/plugin")) });
  }

  return (
    <div className="flex h-full flex-col bg-app-bg">
      <Topbar title={t("title")} />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-5 py-6 md:px-8 md:py-8">
          <nav aria-label={t("breadcrumb")} className="flex min-w-0 items-center gap-1.5 text-[13px] text-ink-subtle">
            <Link to="/plugin" className="hover:text-ink">{t("title")}</Link>
            <ChevronRight className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate text-ink-muted">{manifest?.aina.name ?? ainaId}</span>
          </nav>

          {error ? <p role="alert" className="mt-4 rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger-deep">{error}</p> : null}
          {loading ? <div className="mt-8 h-40 animate-pulse rounded-2xl bg-line/60" /> : null}
          {!loading && !manifest ? <p className="mt-10 text-center text-[14px] text-ink-muted">{t("detail.notFound")}</p> : null}

          {manifest && record ? (
            <>
              <header className="mt-7">
                <AppIcon id={manifest.aina.id} name={manifest.aina.name} size="lg" />
                <div className="mt-4 flex flex-wrap items-start gap-3">
                  {/* A 16rem basis lets the actions wrap below the title on narrow screens instead of squeezing it. */}
                  <div className="min-w-0 flex-1 basis-64">
                    <h2 className="text-[24px] font-semibold text-ink">{manifest.aina.name}</h2>
                    <p className="mt-1 text-[14px] leading-relaxed text-ink-muted">{manifest.aina.description}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    {usable ? (
                      <span className="inline-flex h-9 items-center gap-1.5 rounded-full border border-line px-3 text-[13px] text-ink-muted">
                        <span className="h-1.5 w-1.5 rounded-full bg-success" />
                        {builtin ? t("aina.builtin") : t("aina.installed")}
                      </span>
                    ) : null}
                    <ActionMenu label={t("more", { name: manifest.aina.name })} items={menuItems} />
                    {usable && (builtin || manifest.main_widget) ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void run(async () => navigate(await openAina(ainaId)), () => undefined)}
                        className="btn h-9 rounded-full bg-ink px-4 text-white hover:bg-black"
                      >
                        <AppWindow className="h-4 w-4" />
                        {manifest.aina.id === "unibot-scheduler" ? t("aina.manageTasks") : t("aina.open")}
                      </button>
                    ) : null}
                    {!usable ? (
                      <button type="button" disabled={busy} onClick={() => void run(() => installAina(record))} className="btn h-9 rounded-full bg-ink px-4 text-white hover:bg-black">
                        <Download className="h-4 w-4" />{t("aina.install")}
                      </button>
                    ) : null}
                  </div>
                </div>
              </header>

              <div className="mt-10 space-y-10">
                {manifest.permissions.length ? (
                  <section aria-label={tAina("permissions")}>
                    <h2 className="border-b border-line pb-3 text-[16px] font-semibold text-ink">{tAina("permissions")}</h2>
                    <div className="flex flex-wrap gap-1.5 pt-4">
                      {manifest.permissions.map((permission) => (
                        <span key={permission} className="rounded-full bg-warning-soft px-2.5 py-1 font-mono text-[11px] text-warning-deep">{permission}</span>
                      ))}
                    </div>
                  </section>
                ) : null}
                <AinaCapabilitySections record={record} />
                <section aria-label={t("detail.information")}>
                  <h2 className="border-b border-line pb-3 text-[16px] font-semibold text-ink">{t("detail.information")}</h2>
                  <dl className="grid grid-cols-[6.5rem_minmax(0,1fr)] sm:grid-cols-[10rem_minmax(0,1fr)] gap-x-4 gap-y-3 pt-4 text-[13px]">
                    <InfoRow label={t("detail.version")} value={manifest.aina.version} />
                    <InfoRow
                      label={tAina("runtimeLabel")}
                      value={tAina(builtin ? "runtime.builtin" : manifest.runtime.type === "managed" ? "runtime.managed" : "runtime.remote")}
                    />
                    {manifest.runtime.type === "remote" ? <InfoRow label={tAina("endpoint")} value={manifest.runtime.endpoint} mono /> : null}
                    <InfoRow label={tAina("publisher")} value={manifest.aina.publisher.name} />
                    <InfoRow label="ID" value={manifest.aina.id} mono />
                  </dl>
                </section>
              </div>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function InfoRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <>
      <dt className="text-ink-subtle">{label}</dt>
      <dd className={classNames("min-w-0 break-all text-ink", mono && "font-mono text-[12px]")}>{value}</dd>
    </>
  );
}
