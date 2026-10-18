import { useTranslation } from "react-i18next";
import { useEffect, useMemo, useState } from "react";
import { AppWindow, Play, Send, Sparkles } from "lucide-react";
import { AppIcon } from "@/components/apps/PluginUi";
import { MarkdownContent } from "@/components/chat/MarkdownContent";
import { classNames } from "@/lib/utils";
import type { WidgetActionDefinition, WidgetDefinition } from "@/types";
import { AinaWidgetFrame } from "./AinaWidgetFrame";
import { DocumentOutlineWidget } from "./DocumentOutlineWidget";
import { WidgetFormFields } from "./WidgetFormFields";

interface SessionWidgetRendererProps {
  widget: WidgetDefinition;
  workspaceId?: string | null;
  disabled?: boolean;
  onOpenAina?: (ainaId: string) => void;
  onPrompt?: (prompt: string) => void;
}

export function isClarificationWidget(widget: WidgetDefinition): boolean {
  return widget.id.startsWith("clarification-");
}

export function SessionWidgetRenderer(props: SessionWidgetRendererProps) {
  if (props.widget.kind === "aina_widget") {
    return props.widget.aina_widget ? <AinaWidgetFrame widget={props.widget.aina_widget} onPrompt={props.onPrompt} onOpenAina={props.onOpenAina} /> : null;
  }
  if (props.widget.kind === "document_outline") {
    return <DocumentOutlineWidget widget={props.widget} workspaceId={props.workspaceId} />;
  }
  if (props.widget.kind === "aina_chooser" || props.widget.kind === "app_list") {
    return <AppSuggestions widget={props.widget} disabled={props.disabled} onOpenAina={props.onOpenAina} />;
  }
  if (props.widget.kind === "navigation" && props.widget.id.startsWith("open-") && props.widget.actions.some((action) => action.kind === "open_aina")) {
    return null;
  }
  return <DeclarativeSessionWidgetRenderer {...props} />;
}

/**
 * The agent's suggested apps as one quiet line under its reply: a chip opens the app beside the
 * conversation (or switches to it), and ignoring the line costs nothing.
 */
function AppSuggestions({ widget, disabled = false, onOpenAina }: {
  widget: WidgetDefinition;
  disabled?: boolean;
  onOpenAina?: (ainaId: string) => void;
}) {
  const { t } = useTranslation("common");
  if (!widget.apps.length) return null;
  return (
    <div role="group" aria-label={t("widget.suggestedApps")} className="flex flex-wrap items-center gap-1.5">
      <span className="inline-flex items-center gap-1 text-[12px] text-ink-subtle" title={widget.description || undefined}>
        <Sparkles className="h-3.5 w-3.5" />{t("widget.suggestedApps")}
      </span>
      {widget.apps.map((app) => (
        <button
          key={app.aina_id}
          type="button"
          disabled={disabled}
          onClick={() => onOpenAina?.(app.aina_id)}
          title={app.description}
          aria-label={t("widget.openApp", { name: app.name })}
          className="inline-flex h-7 max-w-full items-center gap-1.5 rounded-full border border-line bg-white pl-1 pr-2.5 text-[12.5px] text-ink transition-colors hover:border-accent-ring hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-60"
        >
          <AppIcon id={app.aina_id} name={app.name} size="sm" />
          <span className="truncate">{app.name}</span>
        </button>
      ))}
    </div>
  );
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
    <section className="overflow-hidden rounded-xl border border-line bg-white p-3" aria-label={widget.title}>
      <header className="flex items-center gap-2">
        <span className="text-ink-muted">
          <AppWindow className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-[12px] font-semibold text-ink">{widget.title}</h3>
        </div>
      </header>
      {widget.description ? <p className="mt-1.5 text-[10.5px] leading-relaxed text-ink-subtle">{widget.description}</p> : null}

      <div className="mt-3">
        {widget.markdown ? <MarkdownContent content={widget.markdown} className="mb-3" /> : null}
        <WidgetFormFields widget={widget} disabled={disabled} values={values} setValues={setValues} />

        {widget.actions.length ? (
          <div className={classNames("flex flex-wrap items-center gap-2", widget.fields.length || widget.markdown ? "mt-3" : "")}>
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
