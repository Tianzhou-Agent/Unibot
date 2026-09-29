import { useTranslation } from "react-i18next";
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  BarChart3,
  Globe,
  ShieldCheck,
  LogOut,
  UserRound,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  Server,
  Trash2,
  X,
} from "lucide-react";
import DebugPage from "@/pages/DebugPage";
import { ProviderEditor } from "@/features/model-settings/ProviderEditor";
import type {
  ModelProvider,
  ModelProviderPayload,
  ModelHealthResult,
  ModelSettingsResponse,
  ProviderType,
} from "@/features/model-settings/types";
import { Topbar } from "@/components/layout/Topbar";
import { api, apiErrorMessage } from "@/lib/api";
import { classNames } from "@/lib/utils";

import i18n, { LANGUAGES, currentLocale } from "@/i18n";
import { useAuth } from "@/lib/auth";
import { useMockSession } from "@/lib/mockSession";

type SettingsTab = "general" | "overview" | "models" | "account";
const SETTINGS_TABS: Array<{ id: SettingsTab; icon: typeof Globe }> = [
  { id: "general", icon: Globe },
  { id: "overview", icon: BarChart3 },
  { id: "models", icon: Server },
  { id: "account", icon: UserRound },
];

export default function SettingsPage() {
  const { t } = useTranslation("settings");
  const [searchParams, setSearchParams] = useSearchParams();
  const tab: SettingsTab = SETTINGS_TABS.some((item) => item.id === searchParams.get("tab")) ? (searchParams.get("tab") as SettingsTab) : "general";
  const [settings, setSettings] = useState<ModelSettingsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editorProvider, setEditorProvider] = useState<ModelProvider | null | undefined>(undefined);
  const [editorError, setEditorError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [busyModel, setBusyModel] = useState<string | null>(null);
  const [modelHealth, setModelHealth] = useState<Record<string, ModelHealthResult>>({});

  const load = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    try {
      setSettings(await api.get<ModelSettingsResponse>("/model-settings?user_id=anonymous&tenant_id=default"));
      setError(null);
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function saveProvider(payload: ModelProviderPayload) {
    setSaving(true);
    setEditorError(null);
    try {
      if (editorProvider) {
        await api.put(`/model-settings/providers/${editorProvider.id}`, payload);
      } else {
        await api.post("/model-settings/providers", payload);
      }
      setEditorProvider(undefined);
      setNotice(editorProvider ? t("notice.updated") : t("notice.created"));
      await load();
    } catch (saveError) {
      setEditorError(apiErrorMessage(saveError));
    } finally {
      setSaving(false);
    }
  }

  async function setDefault(providerId: string, modelId: string) {
    setBusyModel(modelId);
    setNotice(null);
    try {
      await api.post(`/model-settings/providers/${providerId}/models/${modelId}/default`, {
        user_id: "anonymous",
        tenant_id: "default",
      });
      setNotice(t("notice.defaultSwitched"));
      await load();
    } catch (setDefaultError) {
      setError(apiErrorMessage(setDefaultError));
    } finally {
      setBusyModel(null);
    }
  }

  async function deleteProvider(providerId: string) {
    setBusyModel(providerId);
    try {
      await api.delete(`/model-settings/providers/${providerId}?user_id=anonymous&tenant_id=default`);
      setPendingDelete(null);
      setNotice(t("notice.deleted"));
      await load();
    } catch (deleteError) {
      setError(apiErrorMessage(deleteError));
    } finally {
      setBusyModel(null);
    }
  }

  async function checkHealth(providerId: string, modelId: string) {
    setBusyModel(modelId);
    try {
      const result = await api.post<ModelHealthResult>(
        `/model-settings/providers/${providerId}/models/${modelId}/health`,
        { user_id: "anonymous", tenant_id: "default" },
      );
      setModelHealth((current) => ({ ...current, [modelId]: result }));
    } catch (healthError) {
      setError(apiErrorMessage(healthError));
    } finally {
      setBusyModel(null);
    }
  }

  const active = settings?.active_model;
  const activeLabel = active?.source === "unconfigured"
    ? t("noModel")
    : active?.model_name ?? active?.model ?? t("loading");

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-app-bg">
      <Topbar
        title={t("title")}
        badge={tab === "models" ? {
          label: activeLabel,
          tone: active?.source === "unconfigured" ? "warning" : "success",
        } : undefined}
      />

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <nav aria-label={t("navAria")} className="flex shrink-0 gap-1 overflow-x-auto border-b border-line bg-white p-2 md:w-56 md:flex-col md:overflow-visible md:border-b-0 md:border-r md:p-3">
          {SETTINGS_TABS.map(({ id, icon: Icon }) => (
            <button
              key={id}
              type="button"
              aria-current={tab === id ? "page" : undefined}
              onClick={() => setSearchParams(id === "general" ? {} : { tab: id }, { replace: true })}
              className={classNames(
                "flex h-9 shrink-0 items-center gap-2.5 rounded-lg px-3 text-left text-[12.5px] font-semibold transition-colors",
                tab === id ? "bg-accent-soft text-accent" : "text-ink-muted hover:bg-app-soft hover:text-ink",
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {t(`tab.${id}`)}
            </button>
          ))}
        </nav>

        {tab === "overview" ? (
          <main className="min-h-0 flex-1 overflow-hidden"><DebugPage embedded /></main>
        ) : (
        <main className="min-h-0 flex-1 overflow-y-auto p-3 md:p-6">
          <div className="mx-auto w-full max-w-4xl">
          {tab === "general" ? <GeneralPanel /> : null}
          {tab === "account" ? <AccountPanel /> : null}
          {tab === "models" ? (
          <>
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <div className="min-w-0 flex-1">
              <h2 className="text-[16px] font-extrabold text-ink">{t("models.title")}</h2>
              <p className="mt-0.5 text-[11.5px] text-ink-muted">{t("models.desc")}</p>
            </div>
            <button type="button" onClick={() => void load(true)} disabled={refreshing} className="btn-outline h-8 w-8 p-0" aria-label={t("refreshAria")} title={t("refresh")}>
              <RefreshCw className={classNames("h-3.5 w-3.5", refreshing && "animate-spin")} />
            </button>
            <button type="button" onClick={() => { setEditorError(null); setEditorProvider(null); }} className="btn-primary h-8 text-[11.5px]">
              <Plus className="h-3.5 w-3.5" />{t("addProvider")}
            </button>
          </div>

          <section className="mb-4 border-b border-line pb-4" aria-label={t("currentModel")}>
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent-soft text-accent">
                <Server className="h-4 w-4" />
              </div>
              <div>
                <p className="text-[11px] font-bold text-ink-muted">{t("currentDefault")}</p>
                <p className="text-[14px] font-extrabold text-ink">{activeLabel}</p>
              </div>
              {active?.source === "environment" ? (
                <span className="rounded bg-warning-soft px-2 py-1 text-[10.5px] font-bold text-warning-deep">{t("fromEnv")}</span>
              ) : active?.source === "user" ? (
                <span className="rounded bg-success-soft px-2 py-1 text-[10.5px] font-bold text-success-deep">{t("userConfig")}</span>
              ) : null}
              {active?.provider_name ? <span className="text-[11.5px] text-ink-muted">{t("provider", { name: active.provider_name })}</span> : null}
            </div>
          </section>

          {error ? <div className="mb-4 rounded-lg border border-danger-ring bg-danger-soft px-3 py-2 text-[12px] text-danger-deep">{error}</div> : null}
          {notice ? <div className="mb-4 flex items-center gap-2 rounded-lg border border-green-200 bg-success-soft px-3 py-2 text-[12px] text-success-deep"><CheckCircle2 className="h-4 w-4" />{notice}</div> : null}

          <div className="mb-3 flex items-end">
            <div>
              <h2 className="text-[14px] font-extrabold text-ink">Provider</h2>
              <p className="mt-0.5 text-[11.5px] text-ink-muted">{t("providersHint")}</p>
            </div>
            <div className="flex-1" />
            <span className="text-[11px] font-semibold text-ink-subtle">{t("providerCount", { count: settings?.providers.length ?? 0 })}</span>
          </div>

          {loading ? (
            <div className="space-y-3" aria-label={t("loadingAria")}>
              {[0, 1].map((item) => <div key={item} className="h-32 animate-pulse rounded-lg border border-line bg-white" />)}
            </div>
          ) : settings?.providers.length ? (
            <div className="space-y-3">
              {settings.providers.map((provider) => (
                <ProviderSection
                  key={provider.id}
                  provider={provider}
                  activeModelId={active?.model_id ?? null}
                  busyModel={busyModel}
                  confirmingDelete={pendingDelete === provider.id}
                  onEdit={() => { setEditorError(null); setEditorProvider(provider); }}
                  onRequestDelete={() => setPendingDelete(provider.id)}
                  onCancelDelete={() => setPendingDelete(null)}
                  onDelete={() => void deleteProvider(provider.id)}
                  onSetDefault={(modelId) => void setDefault(provider.id, modelId)}
                  health={modelHealth}
                  onCheckHealth={(modelId) => void checkHealth(provider.id, modelId)}
                />
              ))}
            </div>
          ) : (
            <div className="flex min-h-56 flex-col items-center justify-center rounded-lg border border-dashed border-line-strong bg-white px-6 text-center">
              <Server className="h-8 w-8 text-ink-subtle" />
              <h3 className="mt-3 text-[13px] font-extrabold text-ink">{t("emptyTitle")}</h3>
              <p className="mt-1 max-w-md text-[11.5px] leading-5 text-ink-muted">{t("emptyBody")}</p>
              <button type="button" onClick={() => setEditorProvider(null)} className="btn-primary mt-4 h-8 text-[11.5px]"><Plus className="h-3.5 w-3.5" />{t("addProvider")}</button>
            </div>
          )}
          </>
          ) : null}
          </div>
        </main>
        )}
      </div>

      {editorProvider !== undefined ? (
        <ProviderEditor
          provider={editorProvider}
          saving={saving}
          error={editorError}
          onClose={() => setEditorProvider(undefined)}
          onSave={saveProvider}
        />
      ) : null}
    </div>
  );
}

function ProviderSection({
  provider,
  activeModelId,
  busyModel,
  confirmingDelete,
  onEdit,
  onRequestDelete,
  onCancelDelete,
  onDelete,
  onSetDefault,
  health,
  onCheckHealth,
}: {
  provider: ModelProvider;
  activeModelId: string | null;
  busyModel: string | null;
  confirmingDelete: boolean;
  onEdit: () => void;
  onRequestDelete: () => void;
  onCancelDelete: () => void;
  onDelete: () => void;
  onSetDefault: (modelId: string) => void;
  health: Record<string, ModelHealthResult>;
  onCheckHealth: (modelId: string) => void;
}) {
  const { t } = useTranslation("settings");
  const [collapsed, setCollapsed] = useState(true);
  return (
    <section className="overflow-hidden rounded-lg border border-line bg-white shadow-card" aria-label={`Provider ${provider.name}`}>
      <div className="flex w-full items-stretch border-b border-line">
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          aria-expanded={!collapsed}
          className="flex min-w-0 flex-1 flex-wrap items-center gap-3 px-4 py-3 text-left"
        >
          {collapsed ? <ChevronRight className="h-4 w-4 shrink-0 text-ink-subtle" /> : <ChevronDown className="h-4 w-4 shrink-0 text-accent" />}
          <div className="flex h-8 w-8 items-center justify-center rounded bg-app-soft text-ink-muted"><Server className="h-4 w-4" /></div>
          <div className="min-w-40">
            <div className="flex items-center gap-2">
              <h3 className="text-[13px] font-extrabold text-ink">{provider.name}</h3>
              <span className="rounded bg-app-soft px-1.5 py-0.5 text-[9.5px] font-bold uppercase text-ink-muted">{providerTypeLabel(provider.provider_type)}</span>
            </div>
            <p className="mt-0.5 max-w-xl truncate font-mono text-[10.5px] text-ink-subtle">{provider.base_url}</p>
          </div>
          <div className="flex-1" />
          <div className="hidden items-center gap-4 text-[10.5px] text-ink-muted sm:flex">
            <span className="flex items-center gap-1"><KeyRound className="h-3 w-3" />{provider.api_key_masked}</span>
            <span className="flex items-center gap-1"><Clock3 className="h-3 w-3" />{provider.timeout_seconds}s</span>
          </div>
        </button>
        <div className="flex shrink-0 items-center gap-1 pr-4">
          <button type="button" onClick={onEdit} className="btn-ghost h-8 w-8 p-0" aria-label={t("editAria", { name: provider.name })} title={t("editTitle")}><Pencil className="h-3.5 w-3.5" /></button>
          <button type="button" onClick={onRequestDelete} aria-expanded={confirmingDelete} className="btn-ghost h-8 w-8 p-0 text-danger" aria-label={t("deleteAria", { name: provider.name })} title={t("deleteTitle")}><Trash2 className="h-3.5 w-3.5" /></button>
        </div>
      </div>

      {confirmingDelete ? (
        <div className="flex items-center gap-2 border-b border-danger-ring bg-danger-soft px-4 py-2 text-[11.5px] text-danger-deep">
          <span>{t("deleteWarn")}</span><span className="flex-1" />
          <button type="button" onClick={onCancelDelete} disabled={busyModel === provider.id} className="btn-ghost h-7 px-2"><X className="h-3.5 w-3.5" />{t("cancel")}</button>
          <button type="button" onClick={onDelete} disabled={busyModel === provider.id} className="btn-danger-outline h-7 px-2"><Trash2 className="h-3.5 w-3.5" />{t("confirmDelete")}</button>
        </div>
      ) : null}

      {!collapsed ? (
        <div className="divide-y divide-line">
            {provider.models.map((model) => {
              const isActive = model.id === activeModelId;
              const modelStatus = health[model.id];
              return (
                <div key={model.id} className={classNames("grid min-h-14 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-4 py-2.5", isActive && "bg-success-soft/60")}>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[12.5px] font-bold text-ink">{model.name}</span>
                      {isActive ? <span className="flex items-center gap-1 text-[10px] font-bold text-success-deep"><CheckCircle2 className="h-3 w-3" />{t("default")}</span> : null}
                      {!model.enabled ? <span className="text-[10px] font-bold text-ink-subtle">{t("disabled")}</span> : null}
                    </div>
                    <p className="mt-0.5 truncate font-mono text-[10.5px] text-ink-muted">{model.model}</p>
                    {modelStatus ? (
                      <p className={classNames("mt-1 text-[10.5px] font-semibold", modelStatus.status === "healthy" ? "text-success-deep" : "text-danger-deep")}>
                        {modelStatus.status === "healthy" ? t("healthy", { ms: modelStatus.latency_ms }) : t("unhealthy", { error: modelStatus.error ?? t("checkFailed") })}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex items-center gap-2">
                    <button type="button" onClick={() => onCheckHealth(model.id)} disabled={!model.enabled || busyModel === model.id} className="btn-outline h-8 whitespace-nowrap px-2.5 text-[11px] disabled:opacity-40">
                      <RefreshCw className={classNames("h-3.5 w-3.5", busyModel === model.id && "animate-spin")} />{t("healthCheck")}
                    </button>
                  {!isActive ? (
                    <button type="button" onClick={() => onSetDefault(model.id)} disabled={!model.enabled || busyModel === model.id} className="btn-outline h-8 whitespace-nowrap px-2.5 text-[11px] disabled:cursor-not-allowed disabled:opacity-40">
                      <Check className="h-3.5 w-3.5" />{t("setDefault")}
                    </button>
                  ) : null}
                  </div>
                </div>
              );
            })}
        </div>
      ) : null}
    </section>
  );
}

function providerTypeLabel(providerType: ProviderType): string {
  return {
    openai: "OpenAI",
    deepseek: "DeepSeek",
    openrouter: "OpenRouter",
    ollama: "Ollama",
    custom: "Custom",
  }[providerType];
}

function SettingsCard({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <section className="mb-4 rounded-xl border border-line bg-white p-4 shadow-soft">
      <h3 className="text-[13px] font-extrabold text-ink">{title}</h3>
      {description ? <p className="mt-0.5 text-[11.5px] leading-5 text-ink-muted">{description}</p> : null}
      <div className="mt-3">{children}</div>
    </section>
  );
}

function GeneralPanel() {
  const { t } = useTranslation("settings");
  const current = i18n.resolvedLanguage;
  const now = new Date();
  return (
    <>
      <div className="mb-4">
        <h2 className="text-[16px] font-extrabold text-ink">{t("general.title")}</h2>
        <p className="mt-0.5 text-[11.5px] text-ink-muted">{t("general.desc")}</p>
      </div>
      <SettingsCard title={t("language")} description={t("languageHint")}>
        <div role="radiogroup" aria-label={t("language")} className="grid gap-2 sm:grid-cols-2">
          {LANGUAGES.map(({ code, label }) => {
            const selected = current === code;
            return (
              <button
                key={code}
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => void i18n.changeLanguage(code)}
                className={classNames(
                  "flex items-center gap-3 rounded-lg border px-3 py-3 text-left transition-colors",
                  selected ? "border-accent bg-accent-soft" : "border-line hover:bg-app-soft",
                )}
              >
                <span className={classNames("flex h-4 w-4 shrink-0 items-center justify-center rounded-full border", selected ? "border-accent bg-accent text-white" : "border-line-strong")}>
                  {selected ? <Check className="h-3 w-3" /> : null}
                </span>
                <span className="min-w-0">
                  <span className="block text-[12.5px] font-bold text-ink">{label}</span>
                  <span className="block text-[10.5px] text-ink-muted">{t(`langDesc.${code}`)}</span>
                </span>
              </button>
            );
          })}
        </div>
      </SettingsCard>
      <SettingsCard title={t("formats.title")} description={t("formats.desc")}>
        <dl className="grid gap-x-6 gap-y-2 text-[12px] sm:grid-cols-[auto_1fr]">
          <dt className="text-ink-muted">{t("formats.date")}</dt>
          <dd className="font-mono text-ink">{new Intl.DateTimeFormat(currentLocale(), { dateStyle: "long", timeStyle: "short" }).format(now)}</dd>
          <dt className="text-ink-muted">{t("formats.number")}</dt>
          <dd className="font-mono text-ink">{(1234567.89).toLocaleString(currentLocale())}</dd>
        </dl>
      </SettingsCard>
    </>
  );
}

function AccountPanel() {
  const { t } = useTranslation("settings");
  const navigate = useNavigate();
  const { profile, isAdmin, toggleRole } = useMockSession();
  const { user, config, logout } = useAuth();
  const name = config.auth_required ? user?.name || user?.email || "" : profile.name;
  const email = config.auth_required ? user?.email : null;
  const canAccessAdmin = config.auth_required ? Boolean(user?.is_admin) : true;
  const role = config.auth_required ? (user?.is_admin ? t("account.roleAdmin") : t("account.roleUser")) : profile.roleLabel;
  return (
    <>
      <div className="mb-4">
        <h2 className="text-[16px] font-extrabold text-ink">{t("account.title")}</h2>
        <p className="mt-0.5 text-[11.5px] text-ink-muted">{t("account.desc")}</p>
      </div>
      <SettingsCard title={t("account.profile")}>
        <div className="flex flex-wrap items-center gap-3">
          {user?.avatar_url ? (
            <img src={user.avatar_url} alt="" className="h-12 w-12 rounded-full border border-line-strong object-cover" referrerPolicy="no-referrer" />
          ) : (
            <span className="flex h-12 w-12 items-center justify-center rounded-full border border-line-strong bg-app-soft text-ink-muted"><UserRound className="h-5 w-5" /></span>
          )}
          <div className="min-w-0 flex-1">
            <p className="truncate text-[14px] font-extrabold text-ink">{name}</p>
            {email ? <p className="truncate text-[11.5px] text-ink-muted">{email}</p> : null}
            <span className={classNames("mt-1 inline-block rounded px-2 py-0.5 text-[10.5px] font-bold", (config.auth_required ? user?.is_admin : isAdmin) ? "bg-accent-soft text-accent" : "bg-app-soft text-ink-muted")}>{role}</span>
          </div>
          {config.auth_required ? (
            <button type="button" onClick={() => void logout().then(() => navigate("/login", { replace: true }))} className="btn-outline h-8 text-[11.5px]">
              <LogOut className="h-3.5 w-3.5" />{t("account.logout")}
            </button>
          ) : null}
        </div>
        {!config.auth_required ? (
          <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-line pt-3">
            <p className="min-w-0 flex-1 text-[11px] text-ink-subtle">{t("account.mockNote")}</p>
            <button type="button" onClick={toggleRole} className="btn-outline h-8 text-[11.5px]">
              {isAdmin ? <UserRound className="h-3.5 w-3.5" /> : <ShieldCheck className="h-3.5 w-3.5" />}
              {t("account.switchTo", { role: isAdmin ? t("account.roleUser") : t("account.roleAdmin") })}
            </button>
          </div>
        ) : null}
      </SettingsCard>
      {canAccessAdmin ? (
      <SettingsCard title={t("account.shortcuts")}>
        <div className="grid gap-2 sm:grid-cols-2">
          {canAccessAdmin ? (
            <Link to="/admin/observability" className="flex items-center gap-3 rounded-lg border border-line px-3 py-3 transition-colors hover:bg-app-soft">
              <ShieldCheck className="h-4 w-4 shrink-0 text-accent" />
              <span className="min-w-0">
                <span className="block text-[12.5px] font-bold text-ink">{t("account.admin")}</span>
                <span className="block text-[10.5px] text-ink-muted">{t("account.adminDesc")}</span>
              </span>
            </Link>
          ) : null}
        </div>
      </SettingsCard>
      ) : null}
    </>
  );
}
