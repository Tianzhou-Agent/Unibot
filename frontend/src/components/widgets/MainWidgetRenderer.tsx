import { useEffect, useMemo, useState } from "react";
import { Play, Send } from "lucide-react";
import { MarkdownContent } from "@/components/chat/MarkdownContent";
import { classNames } from "@/lib/utils";
import type { DocumentTaskContext, WidgetActionDefinition, WidgetDefinition } from "@/types";
import { AssistantMainWidget } from "./AssistantMainWidget";
import { DocumentWidget } from "./DocumentWidget";
import { MemoryMainWidget } from "./MemoryMainWidget";
import { WidgetFormFields } from "./WidgetFormFields";
import { ScheduledAinaMainWidget } from "@/pages/ScheduledAinaPage";
import { CodeRunnerMainWidget } from "./CodeRunnerMainWidget";

export function MainWidgetRenderer({
  ainaId,
  widget,
  disabled = false,
  onOpenAina,
  onPrompt,
  onDocumentTaskContextChange,
  refreshToken,
}: {
  ainaId: string;
  widget: WidgetDefinition;
  disabled?: boolean;
  onOpenAina?: (ainaId: string) => void;
  onPrompt?: (prompt: string) => void;
  onDocumentTaskContextChange?: (context: DocumentTaskContext | null) => void;
  refreshToken?: string | null;
}) {
  if (ainaId === "unibot-scheduler") return <ScheduledAinaMainWidget />;
  if (ainaId === "unibot-code-runner") return <CodeRunnerMainWidget />;
  if (widget.kind === "document") return <DocumentWidget disabled={disabled} refreshToken={refreshToken} onTaskContextChange={onDocumentTaskContextChange} />;
  if (widget.kind === "memory") return <MemoryMainWidget disabled={disabled} onPrompt={onPrompt} />;
  if (ainaId === "unibot-assistant") {
    return <AssistantMainWidget widget={widget} disabled={disabled} onOpenAina={onOpenAina} />;
  }

  return <DeclarativeMainWidget widget={widget} disabled={disabled} onOpenAina={onOpenAina} onPrompt={onPrompt} />;
}

function DeclarativeMainWidget({
  widget,
  disabled,
  onOpenAina,
  onPrompt,
}: {
  widget: WidgetDefinition;
  disabled: boolean;
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
    <div className="h-full overflow-y-auto bg-white">
      <div className="mx-auto max-w-4xl p-4">
        <header className="border-b border-line pb-3">
          <h2 className="text-[15px] font-extrabold text-ink">{widget.title}</h2>
          {widget.description ? <p className="mt-1 text-[11.5px] leading-relaxed text-ink-muted">{widget.description}</p> : null}
        </header>
        <div className="pt-4">
          {widget.markdown ? <MarkdownContent content={widget.markdown} className="mb-4" /> : null}
          <WidgetFormFields widget={widget} disabled={disabled} values={values} setValues={setValues} />
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
      </div>
    </div>
  );
}

function interpolatePrompt(template: string, values: Record<string, string>): string {
  return template.replace(/\{([^}]+)\}/g, (_match, key: string) => values[key]?.trim() ?? "");
}
