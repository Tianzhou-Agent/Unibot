import { AppWindow, ArrowRight } from "lucide-react";
import type { ComponentType, Dispatch, SetStateAction } from "react";
import type { WidgetDefinition } from "@/types";
import { MemoryWidget } from "./MemoryWidget";

export interface HostWidgetProps {
  widget: WidgetDefinition;
  disabled: boolean;
  values: Record<string, string>;
  setValues: Dispatch<SetStateAction<Record<string, string>>>;
  onOpenAina?: (ainaId: string) => void;
  onPrompt?: (prompt: string) => void;
}

function AppListWidget({ widget, disabled, onOpenAina }: HostWidgetProps) {
  return (
    <div className="grid gap-2.5 sm:grid-cols-2">
      {widget.apps.map((app) => (
        <button
          key={app.aina_id}
          type="button"
          disabled={disabled}
          onClick={() => onOpenAina?.(app.aina_id)}
          className="group flex min-h-[112px] items-start gap-3 rounded-xl border border-line bg-app-soft p-3 text-left transition hover:border-accent-ring hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-60"
          aria-label={`打开 ${app.name}`}
        >
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white text-accent shadow-sm">
            <AppWindow className="h-4.5 w-4.5" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-2">
              <strong className="truncate text-[13px] text-ink">{app.name}</strong>
              {app.has_main_widget ? <span className="rounded bg-accent-softer px-1.5 py-0.5 text-[9px] font-bold text-accent">UI</span> : null}
            </span>
            <span className="mt-1 line-clamp-2 block text-[10.5px] leading-relaxed text-ink-muted">{app.description}</span>
            <span className="mt-2 flex items-center gap-1.5 font-mono text-[9.5px] text-ink-subtle">
              v{app.version} · {app.publisher}
              <ArrowRight className="ml-auto h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
            </span>
          </span>
        </button>
      ))}
      {!widget.apps.length ? <p className="col-span-full rounded-lg border border-dashed border-line py-8 text-center text-[12px] text-ink-muted">当前没有可用的 AINA 应用。</p> : null}
    </div>
  );
}

function DeclarativeWidget({ widget, disabled, values, setValues }: HostWidgetProps) {
  if (!widget.fields.length) return null;
  return (
    <div className="space-y-3">
      {widget.fields.map((field) => (
        <label key={field.id} className="block space-y-1.5">
          <span className="text-[11.5px] font-bold text-ink">
            {field.label}{field.required ? <span className="ml-1 text-danger">*</span> : null}
          </span>
          {field.input_type === "textarea" ? (
            <textarea
              value={values[field.id] ?? ""}
              onChange={(event) => setValues((current) => ({ ...current, [field.id]: event.target.value }))}
              disabled={disabled}
              rows={5}
              aria-label={field.label}
              placeholder={field.placeholder}
              className="input-soft resize-y text-[12.5px]"
            />
          ) : (
            <input
              value={values[field.id] ?? ""}
              onChange={(event) => setValues((current) => ({ ...current, [field.id]: event.target.value }))}
              disabled={disabled}
              type={field.input_type}
              aria-label={field.label}
              placeholder={field.placeholder}
              className="input-soft text-[12.5px]"
            />
          )}
        </label>
      ))}
    </div>
  );
}

function MemoryHostWidget({ disabled, onPrompt }: HostWidgetProps) {
  return <MemoryWidget disabled={disabled} onPrompt={onPrompt} />;
}

export const HOST_WIDGET_REGISTRY: Record<WidgetDefinition["kind"], ComponentType<HostWidgetProps>> = {
  app_list: AppListWidget,
  form: DeclarativeWidget,
  markdown: DeclarativeWidget,
  panel: DeclarativeWidget,
  navigation: DeclarativeWidget,
  memory: MemoryHostWidget,
};

export function HostWidgetBody(props: HostWidgetProps) {
  const Component = HOST_WIDGET_REGISTRY[props.widget.kind];
  return <Component {...props} />;
}
