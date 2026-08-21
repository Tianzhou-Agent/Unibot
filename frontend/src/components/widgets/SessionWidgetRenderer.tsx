import { useEffect, useMemo, useState } from "react";
import { AppWindow, ArrowRight, Boxes, Play, Send } from "lucide-react";
import { MarkdownContent } from "@/components/chat/MarkdownContent";
import { classNames } from "@/lib/utils";
import type { WidgetActionDefinition, WidgetDefinition } from "@/types";
import { DocumentOutlineWidget } from "./DocumentOutlineWidget";
import { WidgetFormFields } from "./WidgetFormFields";

interface SessionWidgetRendererProps {
  widget: WidgetDefinition;
  workspaceId?: string | null;
  disabled?: boolean;
  onOpenAina?: (ainaId: string) => void;
  onPrompt?: (prompt: string) => void;
}

export function SessionWidgetRenderer(props: SessionWidgetRendererProps) {
  if (props.widget.kind === "document_outline") {
    return <DocumentOutlineWidget widget={props.widget} workspaceId={props.workspaceId} />;
  }
  return <DeclarativeSessionWidgetRenderer {...props} />;
}

function DeclarativeSessionWidgetRenderer({
  widget,
  disabled = false,
  onOpenAina,
  onPrompt,
}: SessionWidgetRendererProps) {
  const initialValues = useMemo(
    () => Object.fromEntries(widget.fields.map((field) => [field.id, field.value ?? ""])),
    [widget.fields],
  );
  const [values, setValues] = useState<Record<string, string>>(initialValues);

  useEffect(() => setValues(initialValues), [initialValues, widget.id]);

  const missingRequired = widget.fields.some((field) => field.required && !values[field.id]?.trim());

  function runAction(action: WidgetActionDefinition) {
    if (disabled) return;
    if (action.kind === "open_aina" && action.aina_id) {
      onOpenAina?.(action.aina_id);
      return;
    }
    if (action.kind === "prompt" && action.prompt && !missingRequired) {
      onPrompt?.(interpolatePrompt(action.prompt, values));
    }
  }

  return (
    <section className="overflow-hidden rounded-lg border border-accent-ring bg-white shadow-soft" aria-label={widget.title}>
      <header className="flex items-start gap-3 border-b border-line bg-accent-soft px-4 py-3.5">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent text-white">
          {widget.kind === "app_list" ? <Boxes className="h-4.5 w-4.5" /> : <AppWindow className="h-4.5 w-4.5" />}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-[14px] font-extrabold text-ink">{widget.title}</h3>
          {widget.description ? <p className="mt-0.5 text-[11.5px] leading-relaxed text-ink-muted">{widget.description}</p> : null}
        </div>
        <span className="rounded-md bg-white px-2 py-1 font-mono text-[9.5px] font-bold uppercase text-accent shadow-sm">
          Widget
        </span>
      </header>

      <div className="p-4">
        {widget.markdown ? <MarkdownContent content={widget.markdown} className="mb-4" /> : null}
        {widget.kind === "app_list" ? (
          <SessionAppList widget={widget} disabled={disabled} onOpenAina={onOpenAina} />
        ) : (
          <WidgetFormFields widget={widget} disabled={disabled} values={values} setValues={setValues} />
        )}

        {widget.actions.length ? (
          <div className={classNames("flex flex-wrap items-center gap-2", widget.fields.length || widget.markdown ? "mt-4" : "")}>
            {widget.actions.map((action) => (
              <button
                key={action.id}
                type="button"
                disabled={disabled || (action.kind === "prompt" && missingRequired)}
                onClick={() => runAction(action)}
                className={classNames(
                  action.style === "secondary" ? "btn-outline" : "btn-primary",
                  "disabled:cursor-not-allowed disabled:opacity-50",
                )}
              >
                {action.kind === "open_aina" ? <Play className="h-4 w-4" /> : <Send className="h-4 w-4" />}
                {action.label}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}

function SessionAppList({
  widget,
  disabled,
  onOpenAina,
}: {
  widget: WidgetDefinition;
  disabled: boolean;
  onOpenAina?: (ainaId: string) => void;
}) {
  return (
    <div className="grid grid-cols-1 gap-2">
      {widget.apps.map((app) => (
        <button
          key={app.aina_id}
          type="button"
          disabled={disabled}
          onClick={() => onOpenAina?.(app.aina_id)}
          className="group flex min-h-[82px] w-full min-w-0 items-center gap-2.5 overflow-hidden rounded-lg border border-line bg-app-soft p-2.5 text-left transition hover:border-accent-ring hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-60"
          aria-label={`打开 ${app.name}`}
        >
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white text-accent shadow-sm">
            <AppWindow className="h-4.5 w-4.5" />
          </span>
          <span className="min-w-0 flex-1">
            <strong className="block truncate text-[13px] text-ink">{app.name}</strong>
            <span className="mt-0.5 line-clamp-2 block text-[10.5px] leading-[1.45] text-ink-muted">{app.description}</span>
            <span className="mt-1 block truncate font-mono text-[9.5px] text-ink-subtle" title={app.aina_id}>{app.aina_id}</span>
          </span>
          <ArrowRight className="h-3.5 w-3.5 shrink-0 text-ink-subtle transition-transform group-hover:translate-x-0.5 group-hover:text-accent" />
        </button>
      ))}
      {!widget.apps.length ? <p className="col-span-full rounded-lg border border-dashed border-line py-8 text-center text-[12px] text-ink-muted">当前没有可用的 AINA 应用。</p> : null}
    </div>
  );
}

function interpolatePrompt(template: string, values: Record<string, string>): string {
  return template.replace(/\{([^}]+)\}/g, (_match, key: string) => values[key]?.trim() ?? "");
}
