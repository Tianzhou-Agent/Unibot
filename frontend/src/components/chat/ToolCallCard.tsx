import { useTranslation } from "react-i18next";
import i18n from "@/i18n";
import {
  AppWindow,
  ChevronRight,
  Database,
  FileText,
  Globe2,
  Search,
  TerminalSquare,
  Wrench,
} from "lucide-react";
import { classNames } from "@/lib/utils";
import type { BackendMessage } from "@/types";

type ToolCall = NonNullable<BackendMessage["tool_calls"]>[number];
type ToolCallState = "queued" | "running" | "success" | "error";

export function ToolCallList({
  calls,
  resultsByCallId,
  compact = false,
  debugMode = false,
}: {
  calls: ToolCall[];
  resultsByCallId: ReadonlyMap<string, BackendMessage>;
  compact?: boolean;
  debugMode?: boolean;
}) {
  const { t } = useTranslation("chat");
  if (!calls.length) return null;
  return (
    <section className="space-y-0.5" aria-label={t("tool.aria")}>
      {calls.map((call) => {
        const result = resultsByCallId.get(call.id);
        return (
          <ToolCallCard
            key={call.id}
            name={call.function.name}
            argumentsText={call.function.arguments}
            resultText={result?.content}
            state={result ? (toolResultIsError(result.content) ? "error" : "success") : "running"}
            compact={compact}
            debugMode={debugMode}
          />
        );
      })}
    </section>
  );
}

export function ToolResultCard({ message, compact = false, debugMode = false }: {
  message: BackendMessage;
  compact?: boolean;
  debugMode?: boolean;
}) {
  const { t } = useTranslation("chat");
  const state = toolResultIsError(message.content) ? "error" : "success";
  return (
    <ToolCallCard
      name={message.name ?? t("tool.capabilityCall")}
      resultText={message.content}
      state={state}
      compact={compact}
      debugMode={debugMode}
    />
  );
}

// One borderless line per call: icon, label and a short summary. Running shimmers, a failure turns the
// summary red with its error, and the status itself is only announced to screen readers.
export function ToolCallCard({
  name,
  argumentsText,
  resultText,
  state,
  compact = false,
  debugMode = false,
}: {
  name: string;
  argumentsText?: string | null;
  resultText?: string | null;
  state: ToolCallState;
  compact?: boolean;
  debugMode?: boolean;
}) {
  const { t } = useTranslation("chat");
  const label = toolLabel(name);
  const summary = state === "error"
    ? summarizeResult(resultText) || statusLabel(state)
    : summarizeArguments(argumentsText) || summarizeResult(resultText);
  const Icon = toolIcon(name);
  const hasDetails = Boolean(argumentsText || resultText);
  const running = state === "running" || state === "queued";

  return (
    <details className="group" aria-label={t("tool.callAria", { name, status: statusLabel(state) })}>
      <summary className={classNames(
        "flex max-w-full cursor-pointer list-none items-center gap-1.5 rounded-md text-ink-muted marker:hidden hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-ring [&::-webkit-details-marker]:hidden",
        compact ? "min-h-6 text-[12px]" : "min-h-7 text-[13px]",
      )}>
        <Icon className={classNames("shrink-0", compact ? "h-3.5 w-3.5" : "h-4 w-4", iconTone(state))} />
        <span className={classNames("shrink-0", running && "thinking-shimmer")}>{label}</span>
        {summary ? (
          <>
            <span className="mx-1 h-0.5 w-0.5 shrink-0 rounded-full bg-ink-subtle" />
            <span
              className={classNames("min-w-0 truncate", state === "error" ? "text-danger" : "text-ink-subtle", running && "thinking-shimmer")}
              title={summary}
            >
              {summary}
            </span>
          </>
        ) : null}
        {hasDetails ? (
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-subtle opacity-0 transition group-open:rotate-90 group-open:opacity-100 group-hover:opacity-100" />
        ) : null}
        {state !== "success" ? <span className="sr-only">{statusLabel(state)}</span> : null}
      </summary>

      {hasDetails ? (
        <div className={classNames("mb-1.5 mt-1 overflow-hidden rounded-lg border border-line bg-app-soft", compact ? "px-3 py-2.5" : "px-4 py-3")}>
          {argumentsText ? <ToolPayload label={t("tool.args")} value={argumentsText} compact={compact} /> : null}
          {resultText ? <ToolPayload label={state === "error" ? t("tool.error") : t("tool.result")} value={resultText} compact={compact} separated={Boolean(argumentsText)} /> : null}
          {debugMode ? (
            <details className="mt-3 border-t border-line pt-2.5">
              <summary className="cursor-pointer text-[10px] text-ink-subtle focus:outline-none">{t("tool.viewRaw")}</summary>
              <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all font-mono text-[9.5px] leading-[1.6] text-ink-muted">
                {name}{argumentsText ? `

${t("tool.args")}
${formatJson(argumentsText)}` : ""}{resultText ? `

${t("tool.result")}
${formatJson(resultText)}` : ""}
              </pre>
            </details>
          ) : null}
        </div>
      ) : null}
    </details>
  );
}

export function toolResultIsError(content: string): boolean {
  try {
    const payload: unknown = JSON.parse(content);
    return typeof payload === "object" && payload !== null && Object.prototype.hasOwnProperty.call(payload, "error");
  } catch {
    return false;
  }
}

export function isToolSequenceContinuation(messages: BackendMessage[], index: number): boolean {
  const current = messages[index];
  if (current?.role !== "assistant" || !current.tool_calls?.length) return false;
  for (let previousIndex = index - 1; previousIndex >= 0; previousIndex -= 1) {
    const previous = messages[previousIndex];
    if (previous.role === "tool") continue;
    return previous.role === "assistant" && Boolean(previous.tool_calls?.length);
  }
  return false;
}

function ToolPayload({ label, value, compact, separated = false }: { label: string; value: string; compact: boolean; separated?: boolean }) {
  return (
    <div className={separated ? "mt-3 border-t border-line pt-3" : ""}>
      <div className="mb-1.5 text-[10px] font-medium text-ink-subtle">{label}</div>
      <pre className={classNames("whitespace-pre-wrap break-all font-mono leading-[1.6] text-ink-muted", compact ? "text-[9.5px]" : "text-[10.5px]")}>
        {formatPayloadPreview(value)}
      </pre>
    </div>
  );
}

function toolLabel(name: string): string {
  const value = name.toLowerCase();
  if (value.startsWith("aina_") || value.includes("open_aina")) return i18n.t("chat:tool.label.openApp");
  if (value.includes("list_app")) return i18n.t("chat:tool.label.listApps");
  if (value.includes("suggest_ainas")) return i18n.t("chat:tool.label.suggestApps");
  if (value.includes("search")) return i18n.t("chat:tool.label.webSearch");
  if (value.includes("browser") || value.includes("fetch") || value.includes("open_url")) return i18n.t("chat:tool.label.openWeb");
  if (value.includes("code") || value.includes("runner") || value.includes("execute") || value.includes("terminal")) return i18n.t("chat:tool.label.runCode");
  if (value.includes("document") || value.includes("file")) {
    if (value.includes("read") || value.includes("list") || value.includes("tree")) return i18n.t("chat:tool.label.readDoc");
    return i18n.t("chat:tool.label.writeDoc");
  }
  if (value.includes("memory")) return i18n.t("chat:tool.label.memory");
  if (value.includes("schedule")) return i18n.t("chat:tool.label.schedule");
  return name;
}

function toolIcon(name: string) {
  const value = name.toLowerCase();
  if (value.startsWith("aina_") || value.includes("open_aina") || value.includes("list_app")) return AppWindow;
  if (value.includes("search")) return Search;
  if (value.includes("browser") || value.includes("fetch") || value.includes("open_url")) return Globe2;
  if (value.includes("code") || value.includes("runner") || value.includes("execute") || value.includes("terminal")) return TerminalSquare;
  if (value.includes("document") || value.includes("file")) return FileText;
  if (value.includes("memory") || value.includes("database")) return Database;
  return Wrench;
}

function summarizeArguments(value?: string | null): string {
  if (!value) return "";
  try {
    const payload = JSON.parse(value) as Record<string, unknown>;
    const keys = ["query", "url", "document_name", "path", "command", "recipient", "name", "id"];
    const parts = keys.flatMap((key) => typeof payload[key] === "string" && payload[key] ? [String(payload[key])] : []);
    if (parts.length) return parts.slice(0, 2).join(" · ");
    const count = Object.keys(payload).length;
    return count ? i18n.t("chat:tool.argCount", { count }) : i18n.t("chat:tool.noArgs");
  } catch {
    return truncate(value, 80);
  }
}

function summarizeResult(value?: string | null): string {
  if (!value) return "";
  try {
    const payload = JSON.parse(value) as Record<string, unknown>;
    if (payload.error && typeof payload.error === "object") {
      const message = (payload.error as Record<string, unknown>).message;
      if (typeof message === "string") return message;
    }
    for (const key of ["message", "status", "document_name", "name"]) {
      if (typeof payload[key] === "string" && payload[key]) return String(payload[key]);
    }
    return i18n.t("chat:tool.resultFields", { count: Object.keys(payload).length });
  } catch {
    return truncate(value, 80);
  }
}

function truncate(value: string, length: number) {
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

function formatJson(value: string): string {
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

function formatPayloadPreview(value: string): string {
  try {
    const payload: unknown = JSON.parse(value);
    if (payload === null || typeof payload !== "object") return truncate(String(payload), 180);
    if (Array.isArray(payload)) return `${i18n.t("chat:tool.items", { count: payload.length })}${payload.length ? ` · ${payload.slice(0, 3).map(previewValue).join(" · ")}` : ""}`;
    const entries = Object.entries(payload as Record<string, unknown>);
    if (!entries.length) return i18n.t("chat:tool.empty");
    return entries.slice(0, 6).map(([key, item]) => `${key}: ${previewValue(item)}`).join("\n");
  } catch {
    return truncate(value, 360);
  }
}

function previewValue(value: unknown): string {
  if (Array.isArray(value)) {
    const preview = value.slice(0, 3).filter((item) => ["string", "number", "boolean"].includes(typeof item)).join(", ");
    return `${i18n.t("chat:tool.items", { count: value.length })}${preview ? ` · ${truncate(preview, 90)}` : ""}`;
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (typeof record.message === "string") return truncate(record.message, 120);
    return i18n.t("chat:tool.fields", { count: Object.keys(record).length });
  }
  return truncate(String(value), 140);
}

function statusLabel(state: ToolCallState) {
  return i18n.t(`chat:tool.status.${state}`);
}



function iconTone(state: ToolCallState) {
  if (state === "running") return "text-accent";
  if (state === "error") return "text-danger";
  return "text-ink-muted";
}
