import { useTranslation } from "react-i18next";
import i18n from "@/i18n";
import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, KeyRound, Plus, RefreshCw, Server, Star, Trash2, X } from "lucide-react";
import type {
  ModelDiscoveryResponse,
  ModelHealthResult,
  ModelProvider,
  ModelProviderPayload,
  ProviderType,
} from "@/features/model-settings/types";
import { api, apiErrorMessage } from "@/lib/api";
import { classNames } from "@/lib/utils";

const MAX_MODELS = 50;
const DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000;

interface PresetModel {
  name: string;
  model: string;
  contextWindowTokens: number;
}

// Presets prefill a new provider. Every type is served through the OpenAI-compatible /chat/completions API,
// so the opencode preset only lists Zen models available on that endpoint. Context sizes the vendor does not
// publish fall back to DEFAULT_CONTEXT_WINDOW_TOKENS and can be edited.
export const PROVIDERS: Array<{
  type: ProviderType;
  label: string;
  name: string;
  baseUrl: string;
  models: PresetModel[];
}> = [
  {
    type: "deepseek", label: "DeepSeek", name: "DeepSeek", baseUrl: "https://api.deepseek.com",
    models: [
      { name: "DeepSeek V4.1 Flash", model: "deepseek-flash", contextWindowTokens: 1_000_000 },
      { name: "DeepSeek V4 Pro", model: "deepseek-v4-pro", contextWindowTokens: 1_000_000 },
    ],
  },
  {
    type: "mimo", label: "MiMo", name: "Xiaomi MiMo", baseUrl: "https://api.xiaomimimo.com/v1",
    models: [
      { name: "MiMo V2.6 Pro", model: "mimo-v2.6-pro", contextWindowTokens: 1_048_576 },
      { name: "MiMo V2.6 Flash", model: "mimo-v2.6-flash", contextWindowTokens: 1_048_576 },
    ],
  },
  {
    type: "opencode", label: "opencode", name: "OpenCode Zen", baseUrl: "https://opencode.ai/inference/v1",
    models: [
      { name: "DeepSeek V4 Pro", model: "deepseek-v4-pro", contextWindowTokens: 1_000_000 },
      { name: "GLM 5.3", model: "glm-5.3", contextWindowTokens: DEFAULT_CONTEXT_WINDOW_TOKENS },
      { name: "Kimi K3", model: "kimi-k3", contextWindowTokens: DEFAULT_CONTEXT_WINDOW_TOKENS },
      { name: "Qwen3.8 Max", model: "qwen3.8-max", contextWindowTokens: DEFAULT_CONTEXT_WINDOW_TOKENS },
    ],
  },
  {
    type: "openai", label: "ChatGPT", name: "ChatGPT", baseUrl: "https://api.openai.com/v1",
    models: [
      { name: "GPT-6 Astra", model: "gpt-6-astra", contextWindowTokens: 1_050_000 },
      { name: "GPT-6.1 Sol", model: "gpt-6.1-sol", contextWindowTokens: 1_050_000 },
      { name: "GPT-6 Luna", model: "gpt-6-luna", contextWindowTokens: 1_050_000 },
    ],
  },
  { type: "openrouter", label: "OpenRouter", name: "OpenRouter", baseUrl: "https://openrouter.ai/api/v1", models: [] },
  { type: "ollama", label: "Ollama", get name() { return i18n.t("provider:preset.ollamaName"); }, baseUrl: "http://127.0.0.1:11434/v1", models: [] },
  { type: "custom", get label() { return i18n.t("provider:preset.customLabel"); }, get name() { return i18n.t("provider:preset.customName"); }, baseUrl: "", models: [] },
];

export function providerTypeLabel(type: ProviderType): string {
  return PROVIDERS.find((item) => item.type === type)?.label ?? type;
}

/** 128000 → "128K", 1047576 → "1M". */
export function formatContextTokens(tokens: number): string {
  if (tokens >= 1_000_000) return `${Number((tokens / 1_000_000).toFixed(1))}M`;
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000)}K`;
  return String(tokens);
}

interface ModelDraft {
  key: string;
  id?: string;
  name: string;
  model: string;
  enabled: boolean;
  contextWindowTokens: number;
  acceptsImages: boolean;
}

/** Step one of adding a provider: pick its type, then the drawer opens prefilled with that preset. */
export function ProviderTypePicker({ onClose, onNext }: { onClose: () => void; onNext: (type: ProviderType) => void }) {
  const { t } = useTranslation("provider");
  const [selected, setSelected] = useState<ProviderType>(PROVIDERS[0].type);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/30 p-3" role="dialog" aria-modal="true" aria-labelledby="provider-type-title">
      <div className="absolute inset-0" onClick={onClose} aria-hidden="true" />
      <form
        onSubmit={(event) => { event.preventDefault(); onNext(selected); }}
        className="relative flex max-h-[calc(100vh-24px)] w-full max-w-[440px] flex-col gap-4 overflow-y-auto rounded-xl border border-line bg-white p-5 shadow-xl"
      >
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-app-soft text-ink-muted"><Server className="h-[18px] w-[18px]" /></span>
          <div className="min-w-0 flex-1">
            <h2 id="provider-type-title" className="text-[15px] font-extrabold text-ink">{t("titleNew")}</h2>
            <p className="mt-0.5 text-[11.5px] text-ink-subtle">{t("pickType")}</p>
          </div>
          <button type="button" onClick={onClose} className="btn-ghost h-8 w-8 shrink-0 p-0 text-ink-subtle" aria-label={t("close")}>
            <X className="h-4 w-4" />
          </button>
        </div>
        <div role="radiogroup" aria-label={t("type")} className="flex flex-col gap-2">
          {PROVIDERS.map(({ type, label }) => {
            const checked = selected === type;
            return (
              <button
                key={type}
                type="button"
                role="radio"
                aria-checked={checked}
                onClick={() => setSelected(type)}
                onDoubleClick={() => onNext(type)}
                className={classNames(
                  "flex items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors",
                  checked ? "border-accent bg-accent-soft" : "border-line-strong hover:bg-app-soft",
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block text-[12.5px] font-bold text-ink">{label}</span>
                  <span className="block text-[10.5px] text-ink-subtle">{t(`typeDesc.${type}`)}</span>
                </span>
                <span className={classNames("h-3.5 w-3.5 shrink-0 rounded-full border", checked ? "border-[4px] border-accent bg-white" : "border-line-strong bg-white")} />
              </button>
            );
          })}
        </div>
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="btn-outline h-8 text-[12px]">{t("cancel")}</button>
          <button type="submit" className="btn-primary h-8 min-w-20 text-[12px]">{t("next")}</button>
        </div>
      </form>
    </div>
  );
}

/** Right-side drawer for creating or editing a provider and its models. */
export function ProviderEditor({
  provider,
  presetType,
  activeModelId,
  health,
  checkingHealth,
  busyModel,
  saving,
  error,
  onClose,
  onSave,
  onSetDefault,
  onCheckHealth,
  onDelete,
}: {
  provider: ModelProvider | null;
  presetType: ProviderType;
  activeModelId: string | null;
  health: Record<string, ModelHealthResult>;
  checkingHealth: boolean;
  busyModel: string | null;
  saving: boolean;
  error: string | null;
  onClose: () => void;
  onSave: (payload: ModelProviderPayload) => Promise<void>;
  onSetDefault: (modelId: string) => void;
  onCheckHealth: (modelIds: string[]) => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation("provider");
  const preset = PROVIDERS.find((item) => item.type === presetType) ?? PROVIDERS[0];
  const providerType = provider?.provider_type ?? preset.type;
  const [name, setName] = useState(provider?.name ?? preset.name);
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? preset.baseUrl);
  const [apiKey, setApiKey] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState(provider?.timeout_seconds ?? 60);
  const [models, setModels] = useState<ModelDraft[]>(
    provider?.models.map((model) => ({
      ...model,
      key: model.id,
      contextWindowTokens: model.context_window_tokens ?? DEFAULT_CONTEXT_WINDOW_TOKENS,
      acceptsImages: model.accepts_images ?? false,
    })) ?? preset.models.map((model) => ({ ...emptyModel(), ...model })),
  );
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [discovering, setDiscovering] = useState(false);
  const [discoveryFeedback, setDiscoveryFeedback] = useState<{ error: boolean; message: string } | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const frame = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving) onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose, saving]);

  function updateModel(key: string, changes: Partial<ModelDraft>) {
    setModels((current) => current.map((model) => (model.key === key ? { ...model, ...changes } : model)));
  }

  function toggleExpanded(key: string, open?: boolean) {
    setExpanded((current) => {
      const next = new Set(current);
      if (open ?? !next.has(key)) next.add(key);
      else next.delete(key);
      return next;
    });
  }

  function addModel() {
    const model = emptyModel();
    setModels((current) => [...current, model]);
    toggleExpanded(model.key, true);
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
    if (models.length === 0) {
      setFormError(t("err.noModels"));
      return;
    }
    // Collapsed rows hide their inputs, so open any incomplete row instead of letting it fail silently.
    const incomplete = models.filter((model) => !model.name.trim() || !model.model.trim());
    if (incomplete.length > 0) {
      setExpanded((current) => new Set([...current, ...incomplete.map((model) => model.key)]));
      setFormError(t("err.incompleteModel"));
      return;
    }
    setFormError(null);
    await onSave({
      user_id: "anonymous",
      tenant_id: "default",
      provider_type: providerType,
      name: name.trim(),
      base_url: baseUrl.trim(),
      api_key: apiKey,
      timeout_seconds: timeoutSeconds,
      models: models.map(({ id, name: modelName, model, enabled, contextWindowTokens, acceptsImages }) => ({
        ...(id ? { id } : {}),
        name: modelName.trim(),
        model: model.trim(),
        enabled,
        context_window_tokens: contextWindowTokens,
        accepts_images: acceptsImages,
      })),
    });
  }

  const savedEnabledIds = provider?.models.filter((model) => model.enabled).map((model) => model.id) ?? [];
  const checked = savedEnabledIds.map((id) => health[id]).filter(Boolean);
  const healthyCount = checked.filter((result) => result.status === "healthy").length;
  const shownError = formError ?? error;

  return (
    <div className="fixed inset-0 z-50">
      <div
        className={classNames("absolute inset-0 bg-slate-950/20 transition-opacity duration-200", mounted ? "opacity-100" : "opacity-0")}
        onClick={() => { if (!saving) onClose(); }}
        aria-hidden="true"
      />
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="provider-editor-title"
        onSubmit={(event) => void submit(event)}
        className={classNames(
          "absolute inset-y-0 right-0 flex w-full max-w-[440px] flex-col border-l border-line bg-white shadow-2xl transition-transform duration-200",
          mounted ? "translate-x-0" : "translate-x-full",
        )}
      >
        <div className="flex shrink-0 items-center gap-3 border-b border-line px-5 py-4">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-app-soft text-ink-muted"><Server className="h-[18px] w-[18px]" /></span>
          <div className="min-w-0 flex-1">
            <h2 id="provider-editor-title" className="truncate text-[15px] font-extrabold text-ink">
              {provider ? name.trim() || provider.name : t("titleNew")}
            </h2>
            <p className="mt-0.5 font-mono text-[10.5px] text-ink-subtle">
              <span className="uppercase">{providerTypeLabel(providerType)}</span> · {t("modelCount", { count: models.length })}
            </p>
          </div>
          <button type="button" onClick={onClose} disabled={saving} className="btn-ghost h-8 w-8 shrink-0 p-0 text-ink-subtle" aria-label={t("close")}>
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {shownError ? <div className="mb-4 rounded-lg border border-danger-ring bg-danger-soft px-3 py-2 text-[12px] text-danger-deep">{shownError}</div> : null}

          <div className="flex flex-col gap-3.5">
            <Field label={t("displayName")}>
              <input value={name} onChange={(event) => setName(event.target.value)} className="input-soft text-[12.5px]" required maxLength={100} aria-label={t("providerNameAria")} />
            </Field>
            <Field label="Base URL">
              <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} className="input-soft font-mono text-[12px]" required type="url" placeholder="https://api.example.com/v1" aria-label="Base URL" />
            </Field>
            <Field label="API Key" note={provider?.has_api_key ? t("keyNote", { masked: provider.api_key_masked }) : undefined}>
              <div className="relative">
                <KeyRound className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-ink-subtle" />
                <input value={apiKey} onChange={(event) => setApiKey(event.target.value)} className="input-soft pl-9 font-mono text-[12px]" type="password" autoComplete="new-password" placeholder={provider?.has_api_key ? t("replaceKey") : t("keyOptional")} aria-label="API Key" />
              </div>
            </Field>
            <Field label={t("timeout")}>
              <input value={timeoutSeconds} onChange={(event) => setTimeoutSeconds(Number(event.target.value))} className="input-soft text-[12.5px]" required type="number" min={1} max={600} aria-label={t("timeoutAria")} />
            </Field>
          </div>

          <div className="mt-6 flex items-center gap-1">
            <h3 className="text-[11px] font-bold uppercase tracking-wide text-ink-subtle">{t("modelsCaption", { count: models.length })}</h3>
            <span className="flex-1" />
            <button type="button" onClick={() => void discoverModels()} disabled={discovering || saving} className="btn-ghost h-7 gap-1.5 px-2 text-[11.5px] text-ink-muted disabled:opacity-50">
              <RefreshCw className={classNames("h-3.5 w-3.5", discovering && "animate-spin")} />{discovering ? t("fetching") : t("autoFetch")}
            </button>
            <button type="button" onClick={addModel} disabled={models.length >= MAX_MODELS} className="btn-ghost h-7 gap-1.5 px-2 text-[11.5px] text-ink-muted disabled:opacity-50">
              <Plus className="h-3.5 w-3.5" />{t("addModel")}
            </button>
          </div>
          <p className="mt-0.5 text-[10.5px] leading-4 text-ink-subtle">{t("modelsHint")}</p>
          {discoveryFeedback ? (
            <p className={classNames("mt-2 text-[11px]", discoveryFeedback.error ? "text-danger-deep" : "text-success-deep")}>
              {discoveryFeedback.message}
            </p>
          ) : null}

          {models.length === 0 ? (
            <div className="mt-3 rounded-lg border border-dashed border-line-strong px-4 py-5 text-center text-[11.5px] text-ink-subtle">
              {t("noModels")}
            </div>
          ) : (
            <ul className="mt-3 flex flex-col gap-1.5">
              {models.map((model, index) => {
                const open = expanded.has(model.key);
                const isDefault = Boolean(model.id) && model.id === activeModelId;
                const savedEnabled = provider?.models.some((saved) => saved.id === model.id && saved.enabled);
                const status = model.id ? health[model.id] : undefined;
                const n = index + 1;
                return (
                  <li key={model.key} className={classNames("rounded-lg border", open ? "border-line-strong bg-app-soft/60" : "border-line-strong bg-white")}>
                    <div className="flex items-center gap-2 px-2.5 py-2">
                      <button
                        type="button"
                        onClick={() => toggleExpanded(model.key)}
                        aria-expanded={open}
                        aria-label={t("editModel", { n })}
                        className="flex min-w-0 flex-1 items-center gap-2 text-left"
                      >
                        {open ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-ink-subtle" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-subtle" />}
                        <span className="min-w-0">
                          <span className={classNames("block truncate text-[12.5px] font-bold", model.enabled ? "text-ink" : "text-ink-subtle")}>
                            {model.name.trim() || t("untitledModel")}
                          </span>
                          <span className="flex items-center gap-1.5 truncate font-mono text-[10px] text-ink-subtle">
                            {status ? <span className={classNames("h-1.5 w-1.5 shrink-0 rounded-full", status.status === "healthy" ? "bg-success" : "bg-danger")} title={healthText(t, status)} /> : null}
                            <span className="truncate">
                              {[model.model.trim() || "—", formatContextTokens(model.contextWindowTokens), !model.enabled ? t("disabled") : null].filter(Boolean).join(" · ")}
                            </span>
                          </span>
                        </span>
                      </button>
                      {isDefault ? (
                        <span className="flex shrink-0 items-center gap-1 text-[10.5px] font-bold text-accent"><Star className="h-3 w-3 fill-current" />{t("default")}</span>
                      ) : provider && model.id ? (
                        <button
                          type="button"
                          onClick={() => onSetDefault(model.id!)}
                          disabled={!model.enabled || !savedEnabled || busyModel === model.id}
                          title={!savedEnabled ? t("setDefaultUnsaved") : undefined}
                          className="shrink-0 rounded px-1.5 py-0.5 text-[10.5px] font-semibold text-ink-muted transition-colors hover:bg-accent-soft hover:text-accent disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-ink-muted"
                        >
                          {t("setDefault")}
                        </button>
                      ) : null}
                      <Switch
                        checked={model.enabled}
                        onChange={(enabled) => updateModel(model.key, { enabled })}
                        label={t("enableModel", { n })}
                      />
                    </div>
                    {open ? (
                      <div className="grid grid-cols-2 gap-2.5 border-t border-line px-2.5 pb-2.5 pt-2.5">
                        <Field label={t("displayName")} small>
                          <input value={model.name} onChange={(event) => updateModel(model.key, { name: event.target.value })} className="input-soft bg-white py-1.5 text-[12px]" required placeholder={t("modelNamePlaceholder")} aria-label={t("modelNameAria", { n })} />
                        </Field>
                        <Field label={t("modelId")} small>
                          <input value={model.model} onChange={(event) => updateModel(model.key, { model: event.target.value })} className="input-soft bg-white py-1.5 font-mono text-[11.5px]" required placeholder={t("modelIdPlaceholder")} aria-label={t("modelIdAria", { n })} />
                        </Field>
                        <Field label={t("context")} small>
                          <input
                            value={model.contextWindowTokens}
                            onChange={(event) => updateModel(model.key, { contextWindowTokens: Number(event.target.value) })}
                            className="input-soft bg-white py-1.5 font-mono text-[11.5px]"
                            required
                            type="number"
                            min={4096}
                            max={10_000_000}
                            aria-label={t("contextAria", { n })}
                          />
                        </Field>
                        <label className="col-span-2 flex items-start gap-2 text-[11.5px] text-ink-muted">
                          <input
                            type="checkbox"
                            className="mt-0.5"
                            checked={model.acceptsImages}
                            onChange={(event) => updateModel(model.key, { acceptsImages: event.target.checked })}
                            aria-label={t("acceptsImagesAria", { n })}
                          />
                          <span><span className="font-semibold text-ink">{t("acceptsImages")}</span> · {t("acceptsImagesHint")}</span>
                        </label>
                        <div className="col-span-2 flex items-end justify-end">
                          <button
                            type="button"
                            onClick={() => setModels((current) => current.filter((item) => item.key !== model.key))}
                            title={t("deleteModelTitle")}
                            className="btn-ghost h-8 gap-1.5 px-2 text-[11.5px] text-danger hover:bg-danger-soft"
                            aria-label={t("deleteModel", { n })}
                          >
                            <Trash2 className="h-3.5 w-3.5" />{t("deleteModelTitle")}
                          </button>
                        </div>
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}

          {provider ? (
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => onCheckHealth(savedEnabledIds)}
                disabled={checkingHealth || savedEnabledIds.length === 0}
                className="btn-outline h-8 px-2.5 text-[11.5px] disabled:opacity-40"
              >
                <RefreshCw className={classNames("h-3.5 w-3.5", checkingHealth && "animate-spin")} />{t("healthCheck")}
              </button>
              {checked.length > 0 ? (
                <span className={classNames("text-[11px] font-semibold", healthyCount === checked.length ? "text-success-deep" : "text-danger-deep")}>
                  {t("healthSummary", { healthy: healthyCount, total: checked.length })}
                </span>
              ) : (
                <span className="text-[11px] text-ink-subtle">{t("healthHint")}</span>
              )}
            </div>
          ) : null}
        </div>

        {confirmingDelete && provider ? (
          <div className="flex shrink-0 flex-wrap items-center gap-2 border-t border-danger-ring bg-danger-soft px-5 py-3 text-[11.5px] text-danger-deep">
            <span className="min-w-0 flex-1">{t("deleteWarn")}</span>
            <button type="button" onClick={() => setConfirmingDelete(false)} disabled={busyModel === provider.id} className="btn-ghost h-7 px-2 text-[11.5px]">{t("cancel")}</button>
            <button type="button" onClick={onDelete} disabled={busyModel === provider.id} className="btn-danger-outline h-7 px-2 text-[11.5px]"><Trash2 className="h-3.5 w-3.5" />{t("confirmDelete")}</button>
          </div>
        ) : null}
        <div className="flex shrink-0 items-center gap-2 border-t border-line px-5 py-3">
          {provider ? (
            <button
              type="button"
              onClick={() => setConfirmingDelete(true)}
              aria-expanded={confirmingDelete}
              disabled={saving}
              className="btn-ghost h-8 gap-1.5 px-2 text-[12px] text-danger hover:bg-danger-soft"
              aria-label={t("deleteAria", { name: provider.name })}
            >
              <Trash2 className="h-3.5 w-3.5" />{t("delete")}
            </button>
          ) : null}
          <span className="flex-1" />
          <button type="button" onClick={onClose} disabled={saving} className="btn-outline h-8 text-[12px]">{t("cancel")}</button>
          <button type="submit" disabled={saving} className="btn-primary h-8 min-w-20 text-[12px]">{saving ? t("saving") : t("save")}</button>
        </div>
      </form>
    </div>
  );
}

export function healthText(t: (key: string, options?: Record<string, unknown>) => string, result: ModelHealthResult): string {
  return result.status === "healthy"
    ? t("settings:healthy", { ms: result.latency_ms })
    : t("settings:unhealthy", { error: result.error ?? t("settings:checkFailed") });
}

function Field({ label, note, small, children }: { label: string; note?: string; small?: boolean; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className={classNames("mb-1 block font-bold text-ink-muted", small ? "text-[10.5px]" : "text-[11px]")}>{label}</span>
      {children}
      {note ? <span className="mt-1 block text-[10.5px] text-ink-subtle">{note}</span> : null}
    </label>
  );
}

function Switch({ checked, onChange, disabled, label }: { checked: boolean; onChange: (checked: boolean) => void; disabled?: boolean; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={classNames(
        "relative h-[18px] w-8 shrink-0 rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-accent-ring disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-accent" : "bg-slate-300",
      )}
    >
      <span className={classNames("absolute top-0.5 h-3.5 w-3.5 rounded-full bg-white shadow transition-[left]", checked ? "left-[16px]" : "left-0.5")} />
    </button>
  );
}

function emptyModel(): ModelDraft {
  return {
    key: globalThis.crypto?.randomUUID?.() ?? `model-${Date.now()}-${Math.random()}`,
    name: "",
    model: "",
    enabled: true,
    contextWindowTokens: DEFAULT_CONTEXT_WINDOW_TOKENS,
    acceptsImages: false,
  };
}
