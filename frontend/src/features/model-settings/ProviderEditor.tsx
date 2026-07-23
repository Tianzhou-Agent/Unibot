import { useEffect, useState } from "react";
import { KeyRound, Plus, RefreshCw, Trash2, X } from "lucide-react";
import type {
  ModelDiscoveryResponse,
  ModelProvider,
  ModelProviderPayload,
  ProviderType,
} from "@/features/model-settings/types";
import { api, apiErrorMessage } from "@/lib/api";

const MAX_MODELS = 50;

const PROVIDERS: Array<{
  type: ProviderType;
  label: string;
  name: string;
  baseUrl: string;
}> = [
  { type: "openai", label: "OpenAI", name: "OpenAI", baseUrl: "https://api.openai.com/v1" },
  { type: "deepseek", label: "DeepSeek", name: "DeepSeek", baseUrl: "https://api.deepseek.com/v1" },
  { type: "openrouter", label: "OpenRouter", name: "OpenRouter", baseUrl: "https://openrouter.ai/api/v1" },
  { type: "ollama", label: "Ollama", name: "本地 Ollama", baseUrl: "http://127.0.0.1:11434/v1" },
  { type: "custom", label: "自定义", name: "自定义 Provider", baseUrl: "" },
];

interface ModelDraft {
  key: string;
  id?: string;
  name: string;
  model: string;
  enabled: boolean;
}

export function ProviderEditor({
  provider,
  saving,
  error,
  onClose,
  onSave,
}: {
  provider: ModelProvider | null;
  saving: boolean;
  error: string | null;
  onClose: () => void;
  onSave: (payload: ModelProviderPayload) => Promise<void>;
}) {
  const [providerType, setProviderType] = useState<ProviderType>(provider?.provider_type ?? "openai");
  const [name, setName] = useState(provider?.name ?? "OpenAI");
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "https://api.openai.com/v1");
  const [apiKey, setApiKey] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState(provider?.timeout_seconds ?? 60);
  const [models, setModels] = useState<ModelDraft[]>(
    provider?.models.map((model) => ({ ...model, key: model.id })) ?? [emptyModel()],
  );
  const [discovering, setDiscovering] = useState(false);
  const [discoveryFeedback, setDiscoveryFeedback] = useState<{ error: boolean; message: string } | null>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving) onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose, saving]);

  function selectProvider(type: ProviderType) {
    setProviderType(type);
    if (provider) return;
    const preset = PROVIDERS.find((item) => item.type === type);
    if (!preset) return;
    setName(preset.name);
    setBaseUrl(preset.baseUrl);
  }

  function updateModel(key: string, changes: Partial<ModelDraft>) {
    setModels((current) => current.map((model) => (model.key === key ? { ...model, ...changes } : model)));
  }

  async function discoverModels() {
    if (!baseUrl.trim()) {
      setDiscoveryFeedback({ error: true, message: "请先填写 Provider 的 Base URL。" });
      return;
    }
    setDiscovering(true);
    setDiscoveryFeedback(null);
    try {
      const response = await api.post<ModelDiscoveryResponse>("/model-settings/providers/discover-models", {
        ...(provider ? { provider_id: provider.id } : {}),
        user_id: "anonymous",
        tenant_id: "default",
        base_url: baseUrl.trim(),
        api_key: apiKey,
        timeout_seconds: timeoutSeconds,
      });
      const retained = models.filter((model) => model.id || model.name.trim() || model.model.trim());
      const existing = new Set(retained.map((model) => model.model.trim().toLowerCase()).filter(Boolean));
      const additions = response.models.filter((model) => {
        const normalized = model.id.trim().toLowerCase();
        if (!normalized || existing.has(normalized)) return false;
        existing.add(normalized);
        return true;
      });
      const imported = additions.slice(0, Math.max(0, MAX_MODELS - retained.length));
      if (imported.length > 0) {
        setModels([
          ...retained,
          ...imported.map((model) => ({ ...emptyModel(), name: model.name, model: model.id })),
        ]);
      }
      if (response.models.length === 0) {
        setDiscoveryFeedback({ error: false, message: "Provider 的 /models 接口未返回模型，可继续手动添加。" });
      } else if (imported.length < additions.length) {
        setDiscoveryFeedback({
          error: false,
          message: `已获取 ${response.models.length} 个模型，受 ${MAX_MODELS} 个上限限制，本次添加 ${imported.length} 个。`,
        });
      } else if (imported.length === 0) {
        setDiscoveryFeedback({ error: false, message: "模型列表已是最新，未发现需要添加的新模型。" });
      } else {
        setDiscoveryFeedback({ error: false, message: `已从 Provider 自动添加 ${imported.length} 个模型。` });
      }
    } catch (discoverError) {
      setDiscoveryFeedback({ error: true, message: apiErrorMessage(discoverError) });
    } finally {
      setDiscovering(false);
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    await onSave({
      user_id: "anonymous",
      tenant_id: "default",
      provider_type: providerType,
      name: name.trim(),
      base_url: baseUrl.trim(),
      api_key: apiKey,
      timeout_seconds: timeoutSeconds,
      models: models.map(({ id, name: modelName, model, enabled }) => ({
        ...(id ? { id } : {}),
        name: modelName.trim(),
        model: model.trim(),
        enabled,
      })),
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-3" role="dialog" aria-modal="true" aria-labelledby="provider-editor-title">
      <form onSubmit={(event) => void submit(event)} className="flex max-h-[calc(100vh-24px)] w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-line bg-white shadow-xl">
        <div className="flex shrink-0 items-center border-b border-line px-4 py-3">
          <div>
            <h2 id="provider-editor-title" className="text-[15px] font-extrabold text-ink">
              {provider ? "编辑 Provider" : "新增 Provider"}
            </h2>
            <p className="mt-0.5 text-[11.5px] text-ink-muted">一个 Provider 可以配置多个模型。</p>
          </div>
          <div className="flex-1" />
          <button type="button" onClick={onClose} disabled={saving} className="btn-ghost h-8 w-8 p-0" aria-label="关闭">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {error ? <div className="mb-4 rounded-lg border border-danger-ring bg-danger-soft px-3 py-2 text-[12px] text-danger-deep">{error}</div> : null}
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <label className="text-[12px] font-bold text-ink">
              Provider 类型
              <select value={providerType} onChange={(event) => selectProvider(event.target.value as ProviderType)} className="input-soft mt-1.5" aria-label="Provider 类型">
                {PROVIDERS.map((item) => <option key={item.type} value={item.type}>{item.label}</option>)}
              </select>
            </label>
            <label className="text-[12px] font-bold text-ink">
              显示名称
              <input value={name} onChange={(event) => setName(event.target.value)} className="input-soft mt-1.5" required maxLength={100} aria-label="Provider 名称" />
            </label>
            <label className="text-[12px] font-bold text-ink md:col-span-2">
              Base URL
              <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} className="input-soft mt-1.5 font-mono text-[12px]" required type="url" placeholder="https://api.example.com/v1" aria-label="Base URL" />
            </label>
            <label className="text-[12px] font-bold text-ink">
              API Key
              <div className="relative mt-1.5">
                <KeyRound className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-ink-subtle" />
                <input value={apiKey} onChange={(event) => setApiKey(event.target.value)} className="input-soft pl-9 font-mono text-[12px]" type="password" autoComplete="new-password" placeholder={provider?.has_api_key ? `保留当前密钥（${provider.api_key_masked}）` : "Ollama 等本地服务可留空"} aria-label="API Key" />
              </div>
            </label>
            <label className="text-[12px] font-bold text-ink">
              超时时间（秒）
              <input value={timeoutSeconds} onChange={(event) => setTimeoutSeconds(Number(event.target.value))} className="input-soft mt-1.5" required type="number" min={1} max={600} aria-label="超时时间" />
            </label>
          </div>

          <div className="mt-6 flex items-center border-b border-line pb-2">
            <div>
              <h3 className="text-[13px] font-extrabold text-ink">模型</h3>
              <p className="text-[11px] text-ink-muted">可从 Provider 的 /models 自动获取，也可手动填写模型 ID。</p>
            </div>
            <div className="flex-1" />
            <button type="button" onClick={() => void discoverModels()} disabled={discovering || saving} className="btn-outline mr-2 h-8 text-[11.5px] disabled:opacity-50">
              <RefreshCw className={`h-3.5 w-3.5 ${discovering ? "animate-spin" : ""}`} />{discovering ? "获取中" : "自动获取"}
            </button>
            <button type="button" onClick={() => setModels((current) => [...current, emptyModel()])} disabled={models.length >= MAX_MODELS} className="btn-outline h-8 text-[11.5px] disabled:opacity-50">
              <Plus className="h-3.5 w-3.5" />添加模型
            </button>
          </div>
          {discoveryFeedback ? (
            <p className={`border-b border-line px-1 py-2 text-[11px] ${discoveryFeedback.error ? "text-danger-deep" : "text-success-deep"}`}>
              {discoveryFeedback.message}
            </p>
          ) : null}
          <div className="divide-y divide-line">
            {models.map((model, index) => (
              <div key={model.key} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_auto] items-end gap-3 py-3">
                <label className="text-[11px] font-bold text-ink-muted">
                  显示名称
                  <input value={model.name} onChange={(event) => updateModel(model.key, { name: event.target.value })} className="input-soft mt-1" required placeholder="例如：GPT-4.1" aria-label={`模型 ${index + 1} 显示名称`} />
                </label>
                <label className="text-[11px] font-bold text-ink-muted">
                  模型 ID
                  <input value={model.model} onChange={(event) => updateModel(model.key, { model: event.target.value })} className="input-soft mt-1 font-mono text-[12px]" required placeholder="例如：gpt-4.1" aria-label={`模型 ${index + 1} ID`} />
                </label>
                <div className="flex h-10 items-center gap-1">
                  <label className="flex items-center gap-1.5 whitespace-nowrap text-[11.5px] font-semibold text-ink-muted">
                    <input type="checkbox" checked={model.enabled} onChange={(event) => updateModel(model.key, { enabled: event.target.checked })} className="h-4 w-4 accent-accent" />启用
                  </label>
                  <button type="button" onClick={() => setModels((current) => current.filter((item) => item.key !== model.key))} disabled={models.length === 1} className="btn-ghost h-8 w-8 p-0 text-danger disabled:opacity-30" aria-label={`删除模型 ${index + 1}`} title="删除模型">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="flex shrink-0 justify-end gap-2 border-t border-line px-4 py-3">
          <button type="button" onClick={onClose} disabled={saving} className="btn-outline">取消</button>
          <button type="submit" disabled={saving} className="btn-primary min-w-24">{saving ? "保存中" : "保存"}</button>
        </div>
      </form>
    </div>
  );
}

function emptyModel(): ModelDraft {
  return {
    key: globalThis.crypto?.randomUUID?.() ?? `model-${Date.now()}-${Math.random()}`,
    name: "",
    model: "",
    enabled: true,
  };
}
