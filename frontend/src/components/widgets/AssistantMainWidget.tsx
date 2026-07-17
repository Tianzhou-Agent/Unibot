import { useMemo, useState } from "react";
import { AppWindow, ArrowRight, Search, X } from "lucide-react";
import type { WidgetDefinition } from "@/types";

export function AssistantMainWidget({
  widget,
  disabled,
  onOpenAina,
}: {
  widget: WidgetDefinition;
  disabled: boolean;
  onOpenAina?: (ainaId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const apps = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return widget.apps;
    return widget.apps.filter((app) =>
      `${app.name} ${app.aina_id} ${app.description}`.toLocaleLowerCase().includes(normalized),
    );
  }, [query, widget.apps]);

  return (
    <div className="flex h-full min-h-0 flex-col bg-white">
      <header className="flex min-h-14 flex-wrap items-center gap-3 border-b border-line px-3 py-2">
        <div className="min-w-0 flex-1">
          <h2 className="text-[13.5px] font-extrabold text-ink">可用应用</h2>
          <p className="text-[10.5px] text-ink-muted">{widget.apps.length} 个 AINA</p>
        </div>
        <label className="flex h-9 w-full items-center gap-2 rounded-lg border border-line-strong bg-app-soft px-3 focus-within:border-accent sm:w-64">
          <Search className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索 AINA"
            aria-label="搜索 AINA"
            className="min-w-0 flex-1 bg-transparent text-[11.5px] outline-none placeholder:text-ink-subtle"
          />
          {query ? (
            <button type="button" onClick={() => setQuery("")} aria-label="清除搜索" title="清除搜索">
              <X className="h-3.5 w-3.5 text-ink-muted" />
            </button>
          ) : null}
        </label>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        <div className="grid gap-2.5 xl:grid-cols-2" aria-label="可用 AINA">
          {apps.map((app) => (
            <button
              key={app.aina_id}
              type="button"
              disabled={disabled || !app.has_main_widget}
              onClick={() => onOpenAina?.(app.aina_id)}
              className="group flex min-h-[108px] items-start gap-3 rounded-lg border border-line bg-white p-3 text-left transition hover:border-accent hover:bg-app-soft disabled:cursor-not-allowed disabled:opacity-50"
              aria-label={`打开 ${app.name}`}
            >
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent">
                <AppWindow className="h-4.5 w-4.5" />
              </span>
              <span className="min-w-0 flex-1">
                <strong className="block truncate text-[13px] text-ink">{app.name}</strong>
                <span className="mt-1 line-clamp-2 block text-[11px] leading-relaxed text-ink-muted">{app.description}</span>
                <span className="mt-2 flex items-center gap-2 font-mono text-[9.5px] text-ink-subtle">
                  {app.aina_id}
                  <ArrowRight className="ml-auto h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
                </span>
              </span>
            </button>
          ))}
        </div>
        {!apps.length ? (
          <div className="flex min-h-56 items-center justify-center text-center text-[12px] text-ink-muted">
            {query ? "没有匹配的 AINA" : "当前没有可用的 AINA"}
          </div>
        ) : null}
      </div>
    </div>
  );
}
