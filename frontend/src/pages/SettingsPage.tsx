import { useTranslation } from "react-i18next";
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronRight,
  BarChart3,
  Globe,
  Info,
  KeyRound,
  ShieldCheck,
  LogOut,
  UserRound,
  Plus,
  RefreshCw,
  Server,
} from "lucide-react";
import DebugPage from "@/pages/DebugPage";
import { DeveloperTokensPanel } from "@/features/developer/DeveloperTokensPanel";
import { ProviderEditor, ProviderTypePicker, formatContextTokens, healthText } from "@/features/model-settings/ProviderEditor";
import type {
  ActiveModel,
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

type SettingsTab = "general" | "overview" | "models" | "account" | "developer";
const SETTINGS_TABS: Array<{ id: SettingsTab; icon: typeof Globe }> = [
  { id: "general", icon: Globe },
  { id: "overview", icon: BarChart3 },
  { id: "models", icon: Server },
  { id: "account", icon: UserRound },
  { id: "developer", icon: KeyRound },
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
  // undefined: closed; null: creating a provider of `presetType`; otherwise the provider being edited.
  const [editorProvider, setEditorProvider] = useState<ModelProvider | null | undefined>(undefined);
  const [presetType, setPresetType] = useState<ProviderType>("deepseek");
  const [pickingType, setPickingType] = useState(false);
  const [editorError, setEditorError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [busyModel, setBusyModel] = useState<string | null>(null);
  const [checkingProvider, setCheckingProvider] = useState<string | null>(null);
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
      setEditorProvider(undefined);
      setNotice(t("notice.deleted"));
      await load();
    } catch (deleteError) {
      setError(apiErrorMessage(deleteError));
    } finally {
      setBusyModel(null);
    }
  }

  async function checkHealth(providerId: string, modelIds: string[]) {
    setCheckingProvider(providerId);
    const results = await Promise.all(modelIds.map(async (modelId): Promise<[string, ModelHealthResult]> => {
      try {
        return [modelId, await api.post<ModelHealthResult>(
          `/model-settings/providers/${providerId}/models/${modelId}/health`,
          { user_id: "anonymous", tenant_id: "default" },
        )];
      } catch (healthError) {
        return [modelId, { status: "unhealthy", checked_at: new Date().toISOString(), latency_ms: 0, error: apiErrorMessage(healthError) }];
      }
    }));
    setModelHealth((current) => ({ ...current, ...Object.fromEntries(results) }));
    setCheckingProvider(null);
  }

  function openEditor(provider: ModelProvider | null) {
    setEditorError(null);
    setEditorProvider(provider);
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
          {tab === "developer" ? <DeveloperTokensPanel /> : null}
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
            <button type="button" onClick={() => setPickingType(true)} className="btn-primary h-8 text-[11.5px]">
              <Plus className="h-3.5 w-3.5" />{t("addProvider")}
            </button>
          </div>

          {error ? <div className="mb-4 rounded-lg border border-danger-ring bg-danger-soft px-3 py-2 text-[12px] text-danger-deep">{error}</div> : null}
          {notice ? <div className="mb-4 flex items-center gap-2 rounded-lg border border-green-200 bg-success-soft px-3 py-2 text-[12px] text-success-deep"><CheckCircle2 className="h-4 w-4" />{notice}</div> : null}

          <CurrentModelCard
            active={active}
            label={activeLabel}
            providers={settings?.providers ?? []}
            health={active?.model_id ? modelHealth[active.model_id] : undefined}
            checking={Boolean(active?.provider_id) && checkingProvider === active?.provider_id}
            onCheckHealth={() => { if (active?.provider_id && active.model_id) void checkHealth(active.provider_id, [active.model_id]); }}
          />

          <div className="mb-2 mt-6 flex items-center px-1">
            <h3 className="text-[11px] font-bold uppercase tracking-wide text-ink-subtle">{t("providerCaption", { count: settings?.providers.length ?? 0 })}</h3>
          </div>

          {loading ? (
            <div className="space-y-2" aria-label={t("loadingAria")}>
              {[0, 1, 2].map((item) => <div key={item} className="h-[58px] animate-pulse rounded-lg border border-line-strong bg-app-soft" />)}
            </div>
          ) : settings?.providers.length ? (
            <ul className="space-y-2">
              {settings.providers.map((provider) => (
                <ProviderRow
                  key={provider.id}
                  provider={provider}
                  activeModelId={active?.model_id ?? null}
                  health={modelHealth}
                  onOpen={() => openEditor(provider)}
                />
              ))}
            </ul>
          ) : (
            <div className="flex min-h-56 flex-col items-center justify-center rounded-lg border border-dashed border-line-strong bg-white px-6 text-center">
              <Server className="h-8 w-8 text-ink-subtle" />
              <h3 className="mt-3 text-[13px] font-extrabold text-ink">{t("emptyTitle")}</h3>
              <p className="mt-1 max-w-md text-[11.5px] leading-5 text-ink-muted">{t("emptyBody")}</p>
              <button type="button" onClick={() => setPickingType(true)} className="btn-primary mt-4 h-8 text-[11.5px]"><Plus className="h-3.5 w-3.5" />{t("addProvider")}</button>
            </div>
          )}

          {settings?.providers.length ? (
            <p className="mt-3 flex items-center gap-1.5 px-1 text-[11px] text-ink-subtle"><Info className="h-3 w-3 shrink-0" />{t("envHint")}</p>
          ) : null}
          </>
          ) : null}
          </div>
        </main>
        )}
      </div>

      {pickingType ? (
        <ProviderTypePicker
          onClose={() => setPickingType(false)}
          onNext={(type) => { setPickingType(false); setPresetType(type); openEditor(null); }}
        />
      ) : null}
      {editorProvider !== undefined ? (
        <ProviderEditor
          key={editorProvider?.id ?? `new-${presetType}`}
          provider={editorProvider}
          presetType={presetType}
          activeModelId={active?.model_id ?? null}
          health={modelHealth}
          checkingHealth={Boolean(editorProvider) && checkingProvider === editorProvider?.id}
          busyModel={busyModel}
          saving={saving}
          error={editorError}
          onClose={() => setEditorProvider(undefined)}
          onSave={saveProvider}
          onSetDefault={(modelId) => { if (editorProvider) void setDefault(editorProvider.id, modelId); }}
          onCheckHealth={(modelIds) => { if (editorProvider) void checkHealth(editorProvider.id, modelIds); }}
          onDelete={() => { if (editorProvider) void deleteProvider(editorProvider.id); }}
        />
      ) : null}
    </div>
  );
}

function CurrentModelCard({
  active,
  label,
  providers,
  health,
  checking,
  onCheckHealth,
}: {
  active: ActiveModel | undefined;
  label: string;
  providers: ModelProvider[];
  health: ModelHealthResult | undefined;
  checking: boolean;
  onCheckHealth: () => void;
}) {
  const { t } = useTranslation("settings");
  const unconfigured = active?.source === "unconfigured";
  const contextTokens = providers
    .find((provider) => provider.id === active?.provider_id)
    ?.models.find((model) => model.id === active?.model_id)?.context_window_tokens;
  const meta = unconfigured
    ? t("unconfiguredHint")
    : [
        active?.provider_name,
        active?.source === "environment" ? t("fromEnv") : active?.source === "user" ? t("userConfig") : null,
        contextTokens ? t("contextLabel", { size: formatContextTokens(contextTokens) }) : null,
      ].filter(Boolean).join(" · ");
  return (
    <section className="flex flex-wrap items-center gap-3 rounded-xl border border-line-strong bg-white px-3.5 py-3 shadow-card" aria-label={t("currentModel")}>
      <span className={classNames("flex h-9 w-9 shrink-0 items-center justify-center rounded-lg", unconfigured ? "bg-warning-soft text-warning-deep" : "bg-accent-soft text-accent")}>
        {unconfigured ? <AlertTriangle className="h-4 w-4" /> : <Server className="h-4 w-4" />}
      </span>
      <div className="min-w-40 flex-1">
        <p className="text-[10.5px] font-bold text-ink-subtle">{t("currentDefault")}</p>
        <p className="truncate text-[13.5px] font-extrabold text-ink">{label}</p>
        {meta ? <p className="mt-0.5 truncate text-[11px] text-ink-subtle">{meta}</p> : null}
      </div>
      {health ? (
        <span className={classNames("flex max-w-full items-center gap-1.5 text-[11px] font-semibold", health.status === "healthy" ? "text-success-deep" : "text-danger-deep")}>
          <span className={classNames("h-1.5 w-1.5 shrink-0 rounded-full", health.status === "healthy" ? "bg-success" : "bg-danger")} />
          <span className="truncate">{healthText(t, health)}</span>
        </span>
      ) : null}
      {active?.source === "user" ? (
        <button type="button" onClick={onCheckHealth} disabled={checking} className="btn-ghost h-7 gap-1.5 px-2 text-[11px] text-ink-muted">
          <RefreshCw className={classNames("h-3.5 w-3.5", checking && "animate-spin")} />{t("healthCheck")}
        </button>
      ) : null}
    </section>
  );
}

function ProviderRow({
  provider,
  activeModelId,
  health,
  onOpen,
}: {
  provider: ModelProvider;
  activeModelId: string | null;
  health: Record<string, ModelHealthResult>;
  onOpen: () => void;
}) {
  const { t } = useTranslation("settings");
  const defaultModel = provider.models.find((model) => model.id === activeModelId);
  const results = provider.models.filter((model) => model.enabled).map((model) => health[model.id]).filter(Boolean);
  const status = results.length === 0 ? "unchecked" : results.every((result) => result.status === "healthy") ? "healthy" : "unhealthy";
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        aria-label={t("editAria", { name: provider.name })}
        className="group flex w-full items-center gap-3 rounded-lg border border-line-strong bg-white px-3 py-2.5 text-left transition-colors hover:border-ink-subtle/40 hover:bg-app-soft"
      >
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-app-soft text-ink-muted group-hover:bg-white"><Server className="h-4 w-4" /></span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-bold text-ink">{provider.name}</span>
          <span className="block truncate font-mono text-[10.5px] text-ink-subtle">{provider.base_url}</span>
        </span>
        <span className="hidden shrink-0 text-[11px] text-ink-muted sm:block">
          {t("modelCount", { count: provider.models.length })}
          {defaultModel ? <> · {t("defaultIs", { name: defaultModel.name })}</> : null}
        </span>
        <span className={classNames(
          "flex shrink-0 items-center gap-1.5 text-[11px]",
          status === "healthy" ? "text-success-deep" : status === "unhealthy" ? "text-danger-deep" : "text-ink-subtle",
        )}>
          <span className={classNames("h-1.5 w-1.5 rounded-full", status === "healthy" ? "bg-success" : status === "unhealthy" ? "bg-danger" : "bg-slate-300")} />
          {t(`status.${status}`)}
        </span>
        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-subtle" />
      </button>
    </li>
  );
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
                onClick={() => {
                  localStorage.setItem("unibot.language", code);
                  void i18n.changeLanguage(code);
                }}
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
