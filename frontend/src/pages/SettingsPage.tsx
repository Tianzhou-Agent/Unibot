import { useCallback, useEffect, useState } from "react";
import {
  Check,
  CheckCircle2,
  Clock3,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  Server,
  Trash2,
  X,
} from "lucide-react";
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

export default function SettingsPage() {
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
      setNotice(editorProvider ? "Provider 已更新。" : "Provider 已创建，请选择一个默认模型。" );
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
      setNotice("默认模型已切换，新对话请求将使用该模型。" );
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
      setNotice("Provider 已删除。" );
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
    ? "未配置模型"
    : active?.model_name ?? active?.model ?? "加载中";

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-app-bg">
      <Topbar
        title="设置"
        badge={{
          label: activeLabel,
          tone: active?.source === "unconfigured" ? "warning" : "success",
        }}
        actions={
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => void load(true)} disabled={refreshing} className="btn-outline h-8 w-8 p-0" aria-label="刷新模型配置" title="刷新">
              <RefreshCw className={classNames("h-3.5 w-3.5", refreshing && "animate-spin")} />
            </button>
            <button type="button" onClick={() => { setEditorError(null); setEditorProvider(null); }} className="btn-primary h-8 text-[11.5px]">
              <Plus className="h-3.5 w-3.5" />新增 Provider
            </button>
          </div>
        }
      />

      <main className="min-h-0 flex-1 overflow-y-auto p-3 md:p-5">
        <div className="mx-auto w-full max-w-5xl">
          <section className="mb-4 border-b border-line pb-4" aria-label="当前模型">
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent-soft text-accent">
                <Server className="h-4 w-4" />
              </div>
              <div>
                <p className="text-[11px] font-bold text-ink-muted">当前默认模型</p>
                <p className="text-[14px] font-extrabold text-ink">{activeLabel}</p>
              </div>
              {active?.source === "environment" ? (
                <span className="rounded bg-warning-soft px-2 py-1 text-[10.5px] font-bold text-warning-deep">来自环境变量</span>
              ) : active?.source === "user" ? (
                <span className="rounded bg-success-soft px-2 py-1 text-[10.5px] font-bold text-success-deep">用户配置</span>
              ) : null}
              {active?.provider_name ? <span className="text-[11.5px] text-ink-muted">Provider：{active.provider_name}</span> : null}
            </div>
          </section>

          {error ? <div className="mb-4 rounded-lg border border-danger-ring bg-danger-soft px-3 py-2 text-[12px] text-danger-deep">{error}</div> : null}
          {notice ? <div className="mb-4 flex items-center gap-2 rounded-lg border border-green-200 bg-success-soft px-3 py-2 text-[12px] text-success-deep"><CheckCircle2 className="h-4 w-4" />{notice}</div> : null}

          <div className="mb-3 flex items-end">
            <div>
              <h2 className="text-[14px] font-extrabold text-ink">Provider</h2>
              <p className="mt-0.5 text-[11.5px] text-ink-muted">按服务提供方管理接口、密钥和可用模型。</p>
            </div>
            <div className="flex-1" />
            <span className="text-[11px] font-semibold text-ink-subtle">{settings?.providers.length ?? 0} 个</span>
          </div>

          {loading ? (
            <div className="space-y-3" aria-label="正在加载模型配置">
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
              <h3 className="mt-3 text-[13px] font-extrabold text-ink">尚未添加 Provider</h3>
              <p className="mt-1 max-w-md text-[11.5px] leading-5 text-ink-muted">添加一个模型服务，并在它的模型列表中选择默认模型。未选择时继续使用环境变量配置。</p>
              <button type="button" onClick={() => setEditorProvider(null)} className="btn-primary mt-4 h-8 text-[11.5px]"><Plus className="h-3.5 w-3.5" />新增 Provider</button>
            </div>
          )}
        </div>
      </main>

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
  return (
    <section className="overflow-hidden rounded-lg border border-line bg-white shadow-card" aria-label={`Provider ${provider.name}`}>
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
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
        <button type="button" onClick={onEdit} className="btn-ghost h-8 w-8 p-0" aria-label={`编辑 ${provider.name}`} title="编辑 Provider"><Pencil className="h-3.5 w-3.5" /></button>
        <button type="button" onClick={onRequestDelete} className="btn-ghost h-8 w-8 p-0 text-danger" aria-label={`删除 ${provider.name}`} title="删除 Provider"><Trash2 className="h-3.5 w-3.5" /></button>
      </div>

      {confirmingDelete ? (
        <div className="flex items-center gap-2 border-b border-danger-ring bg-danger-soft px-4 py-2 text-[11.5px] text-danger-deep">
          <span>删除后该 Provider 的全部模型配置将不可恢复。</span><span className="flex-1" />
          <button type="button" onClick={onCancelDelete} disabled={busyModel === provider.id} className="btn-ghost h-7 px-2"><X className="h-3.5 w-3.5" />取消</button>
          <button type="button" onClick={onDelete} disabled={busyModel === provider.id} className="btn-danger-outline h-7 px-2"><Trash2 className="h-3.5 w-3.5" />确认删除</button>
        </div>
      ) : null}

      <div className="divide-y divide-line">
        {provider.models.map((model) => {
          const isActive = model.id === activeModelId;
          const modelStatus = health[model.id];
          return (
            <div key={model.id} className={classNames("grid min-h-14 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-4 py-2.5", isActive && "bg-success-soft/60")}>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[12.5px] font-bold text-ink">{model.name}</span>
                  {isActive ? <span className="flex items-center gap-1 text-[10px] font-bold text-success-deep"><CheckCircle2 className="h-3 w-3" />默认</span> : null}
                  {!model.enabled ? <span className="text-[10px] font-bold text-ink-subtle">已停用</span> : null}
                </div>
                <p className="mt-0.5 truncate font-mono text-[10.5px] text-ink-muted">{model.model}</p>
                {modelStatus ? (
                  <p className={classNames("mt-1 text-[10.5px] font-semibold", modelStatus.status === "healthy" ? "text-success-deep" : "text-danger-deep")}>
                    {modelStatus.status === "healthy" ? `健康 · ${modelStatus.latency_ms} ms` : `异常 · ${modelStatus.error ?? "检测失败"}`}
                  </p>
                ) : null}
              </div>
              <div className="flex items-center gap-2">
                <button type="button" onClick={() => onCheckHealth(model.id)} disabled={!model.enabled || busyModel === model.id} className="btn-outline h-8 whitespace-nowrap px-2.5 text-[11px] disabled:opacity-40">
                  <RefreshCw className={classNames("h-3.5 w-3.5", busyModel === model.id && "animate-spin")} />健康检测
                </button>
              {!isActive ? (
                <button type="button" onClick={() => onSetDefault(model.id)} disabled={!model.enabled || busyModel === model.id} className="btn-outline h-8 whitespace-nowrap px-2.5 text-[11px] disabled:cursor-not-allowed disabled:opacity-40">
                  <Check className="h-3.5 w-3.5" />设为默认
                </button>
              ) : null}
              </div>
            </div>
          );
        })}
      </div>
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
