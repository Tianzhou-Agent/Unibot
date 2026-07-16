import { useEffect, useMemo, useState } from "react";
import { AppWindow, Boxes, FileText, Play, Send } from "lucide-react";
import { MarkdownContent } from "@/components/chat/MarkdownContent";
import { classNames } from "@/lib/utils";
import type { WidgetActionDefinition, WidgetDefinition } from "@/types";
import { HostWidgetBody } from "./HostWidgetRegistry";

export function WidgetRenderer({
  widget,
  disabled = false,
  onOpenAina,
  onPrompt,
}: {
  widget: WidgetDefinition;
  disabled?: boolean;
  onOpenAina?: (ainaId: string) => void;
  onPrompt?: (prompt: string) => void;
}) {
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
    <section
      className={classNames(
        "overflow-hidden rounded-xl border border-accent-ring bg-white shadow-soft",
        widget.kind === "document" && "flex h-full min-h-[600px] flex-col",
      )}
      aria-label={widget.title}
    >
      <header className="flex items-start gap-3 border-b border-line bg-gradient-to-r from-accent-soft to-white px-4 py-3.5">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-white">
          {widget.kind === "app_list" ? <Boxes className="h-4.5 w-4.5" /> : widget.kind === "document" ? <FileText className="h-4.5 w-4.5" /> : <AppWindow className="h-4.5 w-4.5" />}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-[14px] font-extrabold text-ink">{widget.title}</h3>
          {widget.description ? <p className="mt-0.5 text-[11.5px] leading-relaxed text-ink-muted">{widget.description}</p> : null}
        </div>
        <span className="rounded-md bg-white px-2 py-1 font-mono text-[9.5px] font-bold uppercase text-accent shadow-sm">
          Widget
        </span>
      </header>

      <div className={classNames("p-4", widget.kind === "document" && "min-h-0 flex-1")}>
        {widget.markdown ? <MarkdownContent content={widget.markdown} className="mb-4" /> : null}
        <HostWidgetBody
          widget={widget}
          disabled={disabled}
          values={values}
          setValues={setValues}
          onOpenAina={onOpenAina}
          onPrompt={onPrompt}
        />

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

function interpolatePrompt(template: string, values: Record<string, string>): string {
  return template.replace(/\{([^}]+)\}/g, (_match, key: string) => values[key]?.trim() ?? "");
}
