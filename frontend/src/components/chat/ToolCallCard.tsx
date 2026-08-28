import {
  AppWindow,
  ChevronRight,
  Database,
  FileText,
  Globe2,
  Loader2,
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
  showHeader = true,
  headerCount,
}: {
  calls: ToolCall[];
  resultsByCallId: ReadonlyMap<string, BackendMessage>;
  compact?: boolean;
  debugMode?: boolean;
  showHeader?: boolean;
  headerCount?: number;
}) {
  if (!calls.length) return null;
  return (
    <section className="space-y-2" aria-label="工具调用">
      {showHeader ? (
        <div className="flex items-center gap-1.5 text-[11px] text-ink-subtle">
          <Wrench className="h-3 w-3" />
          <span>工具调用</span>
          <span className="font-mono">{headerCount ?? calls.length}</span>
        </div>
      ) : null}
      <div className="space-y-2">
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
      </div>
    </section>
  );
}

export function ToolResultCard({ message, compact = false, debugMode = false }: {
  message: BackendMessage;
  compact?: boolean;
  debugMode?: boolean;
}) {
  const state = toolResultIsError(message.content) ? "error" : "success";
  return (
    <ToolCallCard
      name={message.name ?? "能力调用"}
      resultText={message.content}
      state={state}
      compact={compact}
      debugMode={debugMode}
    />
  );
}

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
  const label = toolLabel(name);
  const summary = summarizeArguments(argumentsText) || summarizeResult(resultText) || statusLabel(state);
  const Icon = toolIcon(name);
  const hasDetails = Boolean(argumentsText || resultText);

  return (
    <details
      className="group overflow-hidden rounded-xl border border-line bg-white"
      aria-label={`工具调用 ${name} ${statusLabel(state)}`}
      open={state === "running" || state === "error"}
    >
      <summary className={classNames(
        "flex cursor-pointer list-none items-center gap-2.5 px-3.5 marker:hidden hover:bg-app-soft/70 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-ring [&::-webkit-details-marker]:hidden",
        compact ? "min-h-9" : "min-h-10",
      )}>
        <Icon className={classNames("shrink-0", compact ? "h-3.5 w-3.5" : "h-4 w-4", iconTone(state))} />
        <span className={classNames("shrink-0 font-medium text-ink", compact ? "text-[12px]" : "text-[13px]")}>{label}</span>
        <span className="h-0.5 w-0.5 shrink-0 rounded-full bg-ink-subtle" />
        <span className={classNames("min-w-0 flex-1 truncate text-ink-subtle", compact ? "text-[10.5px]" : "text-[12px]")} title={summary}>
          {summary}
        </span>
        <span className={classNames("h-1.5 w-1.5 shrink-0 rounded-full", statusDot(state), state === "running" && "animate-pulse")} />
        <span className={classNames("shrink-0 font-mono", compact ? "text-[9.5px]" : "text-[10.5px]", statusTone(state))}>
          {statusLabel(state)}
        </span>
        {hasDetails ? <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-subtle transition-transform group-open:rotate-90" /> : null}
      </summary>

      {hasDetails ? (
        <div className={classNames("border-t border-line bg-app-soft", compact ? "px-3 py-2.5" : "px-4 py-3")}>
          {argumentsText ? <ToolPayload label="调用参数" value={argumentsText} compact={compact} /> : null}
          {resultText ? <ToolPayload label={state === "error" ? "错误信息" : "返回结果"} value={resultText} compact={compact} separated={Boolean(argumentsText)} /> : null}
          {debugMode ? (
            <details className="mt-3 border-t border-line pt-2.5">
              <summary className="cursor-pointer text-[10px] text-ink-subtle focus:outline-none">查看完整调用数据</summary>
              <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all font-mono text-[9.5px] leading-[1.6] text-ink-muted">
                {name}{argumentsText ? `\n\n调用参数\n${formatJson(argumentsText)}` : ""}{resultText ? `\n\n返回结果\n${formatJson(resultText)}` : ""}
              </pre>
            </details>
          ) : null}
        </div>
      ) : null}
    </details>
  );
}

export function ToolActivityCard({ text, compact = false }: { text: string; compact?: boolean }) {
  return (
    <div className={classNames("flex items-center gap-2.5 rounded-xl border border-line bg-white px-3.5", compact ? "min-h-9" : "min-h-10")} aria-live="polite">
      <Loader2 className="h-4 w-4 shrink-0 animate-spin text-accent" />
      <span className={classNames("font-medium text-ink", compact ? "text-[11.5px]" : "text-[12.5px]")}>工具调用</span>
      <span className="h-0.5 w-0.5 rounded-full bg-ink-subtle" />
      <span className={classNames("min-w-0 flex-1 truncate text-ink-subtle", compact ? "text-[10.5px]" : "text-[12px]")}>{text}</span>
      <span className="font-mono text-[10px] text-accent">运行中</span>
    </div>
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

export function toolSequenceCallCount(messages: BackendMessage[], index: number): number {
  let count = 0;
  for (let currentIndex = index; currentIndex < messages.length; currentIndex += 1) {
    const message = messages[currentIndex];
    if (message.role === "tool") continue;
    if (message.role === "assistant" && message.tool_calls?.length) {
      count += message.tool_calls.length;
      continue;
    }
    break;
  }
  return count;
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
  if (value.startsWith("aina_") || value.includes("open_aina")) return "打开应用";
  if (value.includes("list_app")) return "查询应用";
  if (value.includes("search")) return "联网搜索";
  if (value.includes("browser") || value.includes("fetch") || value.includes("open_url")) return "打开网页";
  if (value.includes("code") || value.includes("runner") || value.includes("execute") || value.includes("terminal")) return "代码执行";
  if (value.includes("document") || value.includes("file")) {
    if (value.includes("read") || value.includes("list") || value.includes("tree")) return "读取文档";
    return "写入文档";
  }
  if (value.includes("memory")) return "记忆管理";
  if (value.includes("schedule")) return "定时任务";
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
    return count ? `${count} 个参数` : "无参数";
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
    return `${Object.keys(payload).length} 个返回字段`;
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
    if (Array.isArray(payload)) return `${payload.length} 项${payload.length ? ` · ${payload.slice(0, 3).map(previewValue).join(" · ")}` : ""}`;
    const entries = Object.entries(payload as Record<string, unknown>);
    if (!entries.length) return "无内容";
    return entries.slice(0, 6).map(([key, item]) => `${key}: ${previewValue(item)}`).join("\n");
  } catch {
    return truncate(value, 360);
  }
}

function previewValue(value: unknown): string {
  if (Array.isArray(value)) {
    const preview = value.slice(0, 3).filter((item) => ["string", "number", "boolean"].includes(typeof item)).join(", ");
    return `${value.length} 项${preview ? ` · ${truncate(preview, 90)}` : ""}`;
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (typeof record.message === "string") return truncate(record.message, 120);
    return `${Object.keys(record).length} 个字段`;
  }
  return truncate(String(value), 140);
}

function statusLabel(state: ToolCallState) {
  return { queued: "排队中", running: "运行中", success: "完成", error: "失败" }[state];
}

function statusDot(state: ToolCallState) {
  return { queued: "bg-ink-subtle", running: "bg-accent", success: "bg-success", error: "bg-danger" }[state];
}

function statusTone(state: ToolCallState) {
  return { queued: "text-ink-subtle", running: "text-accent", success: "text-ink-subtle", error: "text-danger" }[state];
}

function iconTone(state: ToolCallState) {
  if (state === "running") return "text-accent";
  if (state === "error") return "text-danger";
  return "text-ink-muted";
}
