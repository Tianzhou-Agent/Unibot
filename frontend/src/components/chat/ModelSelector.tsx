import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Cpu, Loader2 } from "lucide-react";
import type { ModelProvider, ModelSettingsResponse } from "@/features/model-settings/types";
import { api, apiErrorMessage } from "@/lib/api";
import { classNames } from "@/lib/utils";

const ACTOR = { user_id: "anonymous", tenant_id: "default" };

export function ModelSelector({ disabled }: { disabled?: boolean }) {
  const [settings, setSettings] = useState<ModelSettingsResponse | null>(null);
  const [open, setOpen] = useState(false);
  const [switching, setSwitching] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.get<ModelSettingsResponse>(
        `/model-settings?user_id=${ACTOR.user_id}&tenant_id=${ACTOR.tenant_id}`,
      );
      setSettings(data);
      setError(null);
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
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

  async function selectModel(provider: ModelProvider, modelId: string) {
    if (switching) return;
    setSwitching(modelId);
    setError(null);
    try {
      await api.post(
        `/model-settings/providers/${provider.id}/models/${modelId}/default`,
        ACTOR,
      );
      await load();
      setOpen(false);
    } catch (selectError) {
      setError(apiErrorMessage(selectError));
    } finally {
      setSwitching(null);
    }
  }

  const active = settings?.active_model;
  const activeLabel = !settings
    ? "加载模型…"
    : active?.source === "unconfigured"
      ? "未配置模型"
      : (active?.model_name ?? active?.model ?? "选择模型");

  const visibleProviders = (settings?.providers ?? [])
    .map((provider) => ({
      ...provider,
      models: provider.models.filter((model) => model.enabled),
    }))
    .filter((provider) => provider.models.length > 0);

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        disabled={disabled || !settings}
        onClick={() => setOpen((current) => !current)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`当前模型：${activeLabel}`}
        title={active?.provider_name ? `${active.provider_name} · ${activeLabel}` : activeLabel}
        className={classNames(
          "flex h-8 max-w-[200px] items-center gap-1.5 rounded-lg border border-line bg-white px-2.5 text-[11.5px] font-semibold text-ink transition-colors",
          disabled || !settings ? "cursor-not-allowed opacity-50" : "hover:border-accent hover:text-accent",
        )}
      >
        <Cpu className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
        <span className="truncate">{activeLabel}</span>
        <ChevronDown className={classNames("h-3 w-3 shrink-0 text-ink-muted transition-transform", open && "rotate-180")} />
      </button>

      {open ? (
        <div
          role="listbox"
          aria-label="选择模型"
          className="absolute bottom-full left-0 z-40 mb-1.5 w-64 overflow-hidden rounded-lg border border-line bg-white shadow-card"
        >
          <div className="max-h-80 overflow-y-auto py-1">
            {error ? (
              <p className="mx-2 my-1 rounded bg-danger-soft px-2 py-1.5 text-[10.5px] text-danger-deep">{error}</p>
            ) : null}
            {visibleProviders.length === 0 ? (
              <p className="px-3 py-2 text-[11.5px] text-ink-muted">
                暂无可用模型，请先在「设置」中添加 Provider。
              </p>
            ) : (
              visibleProviders.map((provider) => (
                <div key={provider.id} className="py-1">
                  <div className="px-3 pb-1 pt-1.5 text-[10px] font-bold uppercase tracking-[0.08em] text-ink-subtle">
                    {provider.name}
                  </div>
                  {provider.models.map((model) => {
                    const isActive = active?.source === "user" && active.model_id === model.id;
                    const isBusy = switching === model.id;
                    return (
                      <button
                        key={model.id}
                        type="button"
                        role="option"
                        aria-selected={isActive}
                        disabled={isBusy}
                        onClick={() => void selectModel(provider, model.id)}
                        className={classNames(
                          "flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12px] transition-colors",
                          isActive ? "bg-accent-soft font-bold text-accent" : "text-ink hover:bg-app-soft",
                        )}
                      >
                        <span className="min-w-0 flex-1">
                          <span className="block truncate">{model.name}</span>
                          <span className="block truncate font-mono text-[10px] text-ink-muted">{model.model}</span>
                        </span>
                        {isBusy ? <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent" /> : null}
                        {!isBusy && isActive ? <Check className="h-3.5 w-3.5 shrink-0 text-accent" /> : null}
                      </button>
                    );
                  })}
                </div>
              ))
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
