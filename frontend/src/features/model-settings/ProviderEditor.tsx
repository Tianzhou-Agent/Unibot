import { useTranslation } from "react-i18next";
import i18n from "@/i18n";
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
const DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000;

const PROVIDERS: Array<{
  type: ProviderType;
  label: string;
  name: string;
  baseUrl: string;
}> = [
  { type: "openai", label: "OpenAI", name: "OpenAI", baseUrl: "https://api.openai.com/v1" },
  { type: "deepseek", label: "DeepSeek", name: "DeepSeek", baseUrl: "https://api.deepseek.com" },
  { type: "openrouter", label: "OpenRouter", name: "OpenRouter", baseUrl: "https://openrouter.ai/api/v1" },
  { type: "ollama", label: "Ollama", get name() { return i18n.t("provider:preset.ollamaName"); }, baseUrl: "http://127.0.0.1:11434/v1" },
  { type: "custom", get label() { return i18n.t("provider:preset.customLabel"); }, get name() { return i18n.t("provider:preset.customName"); }, baseUrl: "" },
];

interface ModelDraft {
  key: string;
  id?: string;
  name: string;
  model: string;
  enabled: boolean;
  contextWindowTokens: number;
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
  const { t } = useTranslation("provider");
  const [providerType, setProviderType] = useState<ProviderType>(provider?.provider_type ?? "openai");
  const [name, setName] = useState(provider?.name ?? "OpenAI");
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "https://api.openai.com/v1");
  const [apiKey, setApiKey] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState(provider?.timeout_seconds ?? 60);
  const [models, setModels] = useState<ModelDraft[]>(
    provider?.models.map((model) => ({
      ...model,
      key: model.id,
      contextWindowTokens: model.context_window_tokens ?? DEFAULT_CONTEXT_WINDOW_TOKENS,
    })) ?? [emptyModel()],
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
      setDiscoveryFeedback({ error: true, message: t("err.baseUrl") });
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
      const discoveredById = new Map(
        response.models.map((model) => [model.id.trim().toLowerCase(), model]),
      );
      const retained = models
        .filter((model) => model.id || model.name.trim() || model.model.trim())
        .map((model) => {
          const discovered = discoveredById.get(model.model.trim().toLowerCase());
          return discovered?.context_window_tokens
            ? { ...model, contextWindowTokens: discovered.context_window_tokens }
            : model;
        });
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
          ...imported.map((model) => ({
            ...emptyModel(),
            name: model.name,
            model: model.id,
            contextWindowTokens: model.context_window_tokens ?? DEFAULT_CONTEXT_WINDOW_TOKENS,
          })),
        ]);
      }
      if (response.models.length === 0) {
        setDiscoveryFeedback({ error: false, message: t("found.none") });
      } else if (imported.length < additions.length) {
        setDiscoveryFeedback({
          error: false,
          message: t("found.capped", { total: response.models.length, max: MAX_MODELS, added: imported.length }),
        });
      } else if (imported.length === 0) {
        setDiscoveryFeedback({ error: false, message: t("found.upToDate") });
      } else {
        setDiscoveryFeedback({ error: false, message: t("found.added", { count: imported.length }) });
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
      models: models.map(({ id, name: modelName, model, enabled, contextWindowTokens }) => ({
        ...(id ? { id } : {}),
        name: modelName.trim(),
        model: model.trim(),
        enabled,
        context_window_tokens: contextWindowTokens,
      })),
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-3" role="dialog" aria-modal="true" aria-labelledby="provider-editor-title">
      <form onSubmit={(event) => void submit(event)} className="flex max-h-[calc(100vh-24px)] w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-line bg-white shadow-xl">
        <div className="flex shrink-0 items-center border-b border-line px-4 py-3">
          <div>
            <h2 id="provider-editor-title" className="text-[15px] font-extrabold text-ink">
              {provider ? t("titleEdit") : t("titleNew")}
            </h2>
            <p className="mt-0.5 text-[11.5px] text-ink-muted">{t("hint")}</p>
          </div>
          <div className="flex-1" />
          <button type="button" onClick={onClose} disabled={saving} className="btn-ghost h-8 w-8 p-0" aria-label={t("close")}>
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {error ? <div className="mb-4 rounded-lg border border-danger-ring bg-danger-soft px-3 py-2 text-[12px] text-danger-deep">{error}</div> : null}
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <label className="text-[12px] font-bold text-ink">
              {t("type")}
              <select value={providerType} onChange={(event) => selectProvider(event.target.value as ProviderType)} className="input-soft mt-1.5" aria-label={t("type")}>
                {PROVIDERS.map((item) => <option key={item.type} value={item.type}>{item.label}</option>)}
              </select>
            </label>
            <label className="text-[12px] font-bold text-ink">
              {t("displayName")}
              <input value={name} onChange={(event) => setName(event.target.value)} className="input-soft mt-1.5" required maxLength={100} aria-label={t("providerNameAria")} />
            </label>
            <label className="text-[12px] font-bold text-ink md:col-span-2">
              Base URL
              <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} className="input-soft mt-1.5 font-mono text-[12px]" required type="url" placeholder="https://api.example.com/v1" aria-label="Base URL" />
            </label>
            <label className="text-[12px] font-bold text-ink">
              API Key
              <div className="relative mt-1.5">
                <KeyRound className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-ink-subtle" />
                <input value={apiKey} onChange={(event) => setApiKey(event.target.value)} className="input-soft pl-9 font-mono text-[12px]" type="password" autoComplete="new-password" placeholder={provider?.has_api_key ? t("keepKey", { masked: provider.api_key_masked }) : t("keyOptional")} aria-label="API Key" />
              </div>
            </label>
            <label className="text-[12px] font-bold text-ink">
              {t("timeout")}
              <input value={timeoutSeconds} onChange={(event) => setTimeoutSeconds(Number(event.target.value))} className="input-soft mt-1.5" required type="number" min={1} max={600} aria-label={t("timeoutAria")} />
            </label>
          </div>

          <div className="mt-6 flex items-center border-b border-line pb-2">
            <div>
              <h3 className="text-[13px] font-extrabold text-ink">{t("models")}</h3>
              <p className="text-[11px] text-ink-muted">{t("modelsHint")}</p>
            </div>
            <div className="flex-1" />
            <button type="button" onClick={() => void discoverModels()} disabled={discovering || saving} className="btn-outline mr-2 h-8 text-[11.5px] disabled:opacity-50">
              <RefreshCw className={`h-3.5 w-3.5 ${discovering ? "animate-spin" : ""}`} />{discovering ? t("fetching") : t("autoFetch")}
            </button>
            <button type="button" onClick={() => setModels((current) => [...current, emptyModel()])} disabled={models.length >= MAX_MODELS} className="btn-outline h-8 text-[11.5px] disabled:opacity-50">
              <Plus className="h-3.5 w-3.5" />{t("addModel")}
            </button>
          </div>
          {discoveryFeedback ? (
            <p className={`border-b border-line px-1 py-2 text-[11px] ${discoveryFeedback.error ? "text-danger-deep" : "text-success-deep"}`}>
              {discoveryFeedback.message}
            </p>
          ) : null}
          <div className="divide-y divide-line">
            {models.map((model, index) => (
              <div key={model.key} className="grid grid-cols-1 items-end gap-3 py-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_minmax(140px,0.7fr)_auto]">
                <label className="text-[11px] font-bold text-ink-muted">
                  {t("displayName")}
                  <input value={model.name} onChange={(event) => updateModel(model.key, { name: event.target.value })} className="input-soft mt-1" required placeholder={t("modelNamePlaceholder")} aria-label={t("modelNameAria", { n: index + 1 })} />
                </label>
                <label className="text-[11px] font-bold text-ink-muted">
                  {t("modelId")}
                  <input value={model.model} onChange={(event) => updateModel(model.key, { model: event.target.value })} className="input-soft mt-1 font-mono text-[12px]" required placeholder={t("modelIdPlaceholder")} aria-label={t("modelIdAria", { n: index + 1 })} />
                </label>
                <label className="text-[11px] font-bold text-ink-muted">
                  {t("context")}
                  <input
                    value={model.contextWindowTokens}
                    onChange={(event) => updateModel(model.key, { contextWindowTokens: Number(event.target.value) })}
                    className="input-soft mt-1 font-mono text-[12px]"
                    required
                    type="number"
                    min={4096}
                    max={10_000_000}
                    aria-label={t("contextAria", { n: index + 1 })}
                  />
                </label>
                <div className="flex h-10 items-center gap-1">
                  <label className="flex items-center gap-1.5 whitespace-nowrap text-[11.5px] font-semibold text-ink-muted">
                    <input type="checkbox" checked={model.enabled} onChange={(event) => updateModel(model.key, { enabled: event.target.checked })} className="h-4 w-4 accent-accent" />{t("enabled")}
                  </label>
                  <button type="button" onClick={() => setModels((current) => current.filter((item) => item.key !== model.key))} disabled={models.length === 1} className="btn-ghost h-8 w-8 p-0 text-danger disabled:opacity-30" aria-label={t("deleteModel", { n: index + 1 })} title={t("deleteModelTitle")}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="flex shrink-0 justify-end gap-2 border-t border-line px-4 py-3">
          <button type="button" onClick={onClose} disabled={saving} className="btn-outline">{t("cancel")}</button>
          <button type="submit" disabled={saving} className="btn-primary min-w-24">{saving ? t("saving") : t("save")}</button>
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
    contextWindowTokens: DEFAULT_CONTEXT_WINDOW_TOKENS,
  };
}
