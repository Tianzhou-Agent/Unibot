import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { Play, Send } from "lucide-react";
import { MarkdownContent } from "@/components/chat/MarkdownContent";
import { classNames } from "@/lib/utils";
import type { DocumentTaskContext, WidgetActionDefinition, WidgetDefinition } from "@/types";
import { DocumentWidget } from "./DocumentWidget";
import { MemoryMainWidget } from "./MemoryMainWidget";
import { WidgetFormFields } from "./WidgetFormFields";
import { ScheduledAinaMainWidget } from "@/pages/ScheduledAinaPage";

const CodeRunnerMainWidget = lazy(() =>
  import("./CodeRunnerMainWidget").then((module) => ({ default: module.CodeRunnerMainWidget })),
);
const ImageRecognitionMainWidget = lazy(() =>
  import("./ImageRecognitionMainWidget").then((module) => ({ default: module.ImageRecognitionMainWidget })),
);

export function MainWidgetRenderer({
  ainaId,
  widget,
  workspaceId,
  documentName,
  disabled = false,
  onOpenAina,
  onPrompt,
  onDocumentTaskContextChange,
  refreshToken,
}: {
  ainaId: string;
  widget: WidgetDefinition;
  workspaceId?: string | null;
  documentName?: string | null;
  disabled?: boolean;
  onOpenAina?: (ainaId: string) => void;
  onPrompt?: (prompt: string) => void;
  onDocumentTaskContextChange?: (context: DocumentTaskContext | null) => void;
  refreshToken?: string | null;
}) {
  if (ainaId === "unibot-scheduler") return <ScheduledAinaMainWidget />;
  if (ainaId === "unibot-code-runner") {
    return (
      <Suspense fallback={<div className="flex h-full items-center justify-center text-[12px] text-ink-muted">正在加载代码编辑器…</div>}>
        <CodeRunnerMainWidget workspaceId={workspaceId} />
      </Suspense>
    );
  }
  if (ainaId === "unibot-image-recognition") {
    return (
      <Suspense fallback={<div className="flex h-full items-center justify-center text-[12px] text-ink-muted">正在加载图片识别…</div>}>
        <ImageRecognitionMainWidget />
      </Suspense>
    );
  }
  if (widget.kind === "document") return <DocumentWidget workspaceId={workspaceId} initialDocumentName={documentName} disabled={disabled} refreshToken={refreshToken} onTaskContextChange={onDocumentTaskContextChange} />;
  if (widget.kind === "memory") return <MemoryMainWidget disabled={disabled} onPrompt={onPrompt} />;
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
