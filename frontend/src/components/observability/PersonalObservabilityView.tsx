import i18n, { currentLocale } from "@/i18n";

// Module-level translator: this file's helpers (log/metric builders) run outside React, so a hook is not an option.
const t = (key: string, options?: Record<string, unknown>): string => i18n.t(key, { ns: "personalObs", ...options }) as string;

import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Bot,
  Brain,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Gauge,
  Layers3,
  MessageSquareText,
  Route,
  Sparkles,
  TerminalSquare,
  Wrench,
  XCircle,
} from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { classNames } from "@/lib/utils";
import { getRawLogs } from "@/lib/obsData";
import type { ObsOverview, ObsSessionDetail } from "@/lib/obsData";
import { adaptSessionDetail } from "@/lib/obsAdapter";
import type { ConversationRecord, LLMCallRecord, TraceRecord, TraceSpan } from "@/types";

type PageView = "conversation" | "overview";
type ConversationDimension = "models" | "capabilities" | "errors" | "spans" | "logs";
type Period = "day" | "week" | "month";
type LogRole = "user" | "assistant" | "tool" | "system";

const DEFAULT_CONTEXT_CAPACITY = 128_000;

interface PersonalObservabilityViewProps {
  error: string | null;
  sessionId: string | null;
  conversations: ConversationRecord[];
  traces: TraceRecord[];
  llmCalls: LLMCallRecord[];
  /** 后端聚合 DTO：个人总览（design 12.2），优先于本地 traces/llmCalls 聚合 */
  obsOverview?: ObsOverview | null;
  /** 后端聚合 DTO：对话详情，优先于本地 traces/llmCalls 过滤 */
  obsSession?: ObsSessionDetail | null;
  /** 个人总览周期切换回调（DTO 模式下由父级重新拉取） */
  onOverviewPeriodChange?: (period: "day" | "week" | "month") => void;
  /** 是否通过 URL searchParams 同步 tab/logId/traceId（整页场景默认 true；抽屉内嵌场景传 false 用内部状态，避免污染当前页面 URL） */
  urlParams?: boolean;
}

interface TokenUsage {
  input: number;
  output: number;
  total: number;
  measured: boolean;
  estimated: boolean;
}

interface TokenSample extends TokenUsage {
  id: string;
  model: string;
  conversationId: string | null;
  createdAt: string;
}

interface RawLogEntry {
  id: string;
  traceId: string;
  role: LogRole;
  title: string;
  subtitle: string;
  input?: unknown;
  output?: unknown;
  error?: unknown;
  /** OBS 新链路的完整原始日志定位（otel trace/span id），用于按需加载 /obs/raw-logs */
  rawLog?: { traceId: string; spanId: string };
}

interface DiagnosticError {
  id: string;
  traceId: string;
  logId: string;
  source: string;
  code: string;
  message: string;
}

export function PersonalObservabilityView({
  error,
  sessionId,
  conversations,
  traces,
  llmCalls,
  obsOverview = null,
  obsSession = null,
  onOverviewPeriodChange,
  urlParams = true,
}: PersonalObservabilityViewProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [internalFocus, setInternalFocus] = useState<{ tab: string | null; logId: string | null; traceId: string | null }>({ tab: null, logId: null, traceId: null });
  const [view, setView] = useState<PageView>(() => sessionId ? "conversation" : "overview");
  useEffect(() => {
    setView(sessionId ? "conversation" : "overview");
  }, [sessionId]);

  const syncToUrl = urlParams;
  const tabParam = syncToUrl ? searchParams.get("tab") : internalFocus.tab;
  const focusedLogId = syncToUrl ? searchParams.get("logId") : internalFocus.logId;
  const focusedTraceId = syncToUrl ? searchParams.get("traceId") : internalFocus.traceId;

  function focusRawLog(logId: string, traceId: string) {
    if (syncToUrl) {
      const next = new URLSearchParams(searchParams);
      next.set("tab", "logs");
      next.set("traceId", traceId);
      next.set("logId", logId);
      setSearchParams(next, { replace: true });
    } else {
      setInternalFocus({ tab: "logs", logId, traceId });
    }
  }

  const adaptedSession = useMemo(
    () => (sessionId && obsSession ? adaptSessionDetail(obsSession) : null),
    [obsSession, sessionId],
  );
  const conversationTraces = useMemo(
    () => {
      if (adaptedSession) return adaptedSession.traces;
      return sessionId
        ? traces
          .filter((trace) => trace.conversation_id === sessionId)
          .sort((left, right) => left.created_at.localeCompare(right.created_at))
        : [];
    },
    [adaptedSession, sessionId, traces],
  );
  const conversationCalls = useMemo(
    () => {
      if (adaptedSession) return adaptedSession.calls;
      const traceIds = new Set(conversationTraces.map((trace) => trace.trace_id));
      return llmCalls
        .filter((call) => (call.trace_id != null && traceIds.has(call.trace_id)) || call.context_id === sessionId)
        .sort((left, right) => left.created_at.localeCompare(right.created_at));
    },
    [adaptedSession, conversationTraces, llmCalls, sessionId],
  );
  const conversation = conversations.find((item) => item.id === sessionId);
  const conversationTitle = conversation?.title
    ?? (sessionId ? t("deletedConversation") : t("noConversation"));

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-app-bg">
      <div className="min-h-0 flex-1 overflow-y-auto p-2.5 md:p-4">
        <div className="mx-auto max-w-[1500px] space-y-3">
          {error ? (
            <div className="rounded-lg border border-danger-ring bg-danger-soft p-3 text-[12.5px] text-danger-deep">
              {error}
            </div>
          ) : null}

          <div className="flex min-h-10 flex-wrap items-center justify-end gap-3 px-1" aria-label={t("viewSelectAria")}>
            {view === "overview" ? (
              <h1 className="text-[16px] font-extrabold leading-tight text-ink">{t("personalOverview")}</h1>
            ) : sessionId ? (
              <div className="min-w-0 max-w-[520px] text-left">
                <strong className="block truncate text-[16px] font-extrabold leading-tight text-ink">{conversationTitle}</strong>
                <span className="mt-0.5 hidden truncate font-mono text-[10px] text-ink-subtle sm:block">{t("sessionId")}{sessionId}</span>
              </div>
            ) : null}
            {sessionId ? (
              <Link
                to="/settings?tab=overview"
                className="ml-auto inline-flex items-center rounded-md px-3 py-1.5 text-[12px] font-bold text-ink-muted transition-colors hover:bg-app-soft hover:text-ink"
              >
                {t("personalOverview")}
              </Link>
            ) : null}
          </div>

          {view === "conversation" && sessionId ? (
            <ConversationView
              conversation={conversation}
              traces={conversationTraces}
              calls={conversationCalls}
              initialDimension={tabParam === "logs" ? "logs" : undefined}
              focusedLogId={focusedLogId}
              focusedTraceId={focusedTraceId}
              onFocusRawLog={focusRawLog}
            />
          ) : (
            <PersonalOverview traces={traces} calls={llmCalls} overview={obsOverview} onPeriodChange={onOverviewPeriodChange} />
          )}
        </div>
      </div>
    </div>
  );
}

function ConversationView({
  conversation,
  traces,
  calls,
  initialDimension,
  focusedLogId,
  focusedTraceId,
  onFocusRawLog,
}: {
  conversation?: ConversationRecord;
  traces: TraceRecord[];
  calls: LLMCallRecord[];
  initialDimension?: ConversationDimension;
  focusedLogId: string | null;
  focusedTraceId: string | null;
  onFocusRawLog: (logId: string, traceId: string) => void;
}) {
  const [dimension, setDimension] = useState<ConversationDimension>(initialDimension ?? "models");
  const allSpans = traces.flatMap((trace) => trace.spans);
  const capabilities = capabilityMetrics(allSpans);
  const capabilityTotals = countCapabilityTypes(capabilities);
  const modelMetrics = buildModelMetrics(traces, calls);
  const errors = traces.flatMap((trace) => buildErrors(
    trace,
    calls.filter((call) => call.trace_id === trace.trace_id),
  ));
  const rawLogs = traces.flatMap((trace) => buildRawLogs(
    trace,
    calls.filter((call) => call.trace_id === trace.trace_id),
  ));
  const resolvedFocusedLogId = focusedLogId
    ?? (focusedTraceId
      ? errors.find((item) => item.traceId === focusedTraceId)?.logId
        ?? rawLogs.find((entry) => entry.traceId === focusedTraceId && entry.error != null)?.id
        ?? rawLogs.find((entry) => entry.traceId === focusedTraceId)?.id
        ?? null
      : null);
  const spanCount = allSpans.length;

  useEffect(() => {
    if (initialDimension === "logs" || focusedLogId || focusedTraceId) setDimension("logs");
  }, [focusedLogId, focusedTraceId, initialDimension]);

  return (
    <div className="space-y-3">
      {conversation || traces.length || calls.length ? (
        <>
          <ConversationOverview conversation={conversation} traces={traces} calls={calls} />

          <div>
            <div className="overflow-x-auto bg-app-bg px-1 pt-1 pb-px" role="tablist" aria-label={t("dimensionsAria")}>
            <div className="flex min-w-max gap-1">
              <DimensionTab active={dimension === "models"} onClick={() => setDimension("models")}>{t("tab.models")}</DimensionTab>
              <DimensionTab active={dimension === "capabilities"} onClick={() => setDimension("capabilities")}>{t("tab.capabilities")}</DimensionTab>
              <DimensionTab active={dimension === "errors"} onClick={() => setDimension("errors")} count={errors.length}>{t("tab.errors")}</DimensionTab>
              <DimensionTab active={dimension === "spans"} onClick={() => setDimension("spans")}>{t("tab.spans")}</DimensionTab>
              <DimensionTab active={dimension === "logs"} onClick={() => setDimension("logs")}>{t("tab.logs")}</DimensionTab>
            </div>
          </div>

          {dimension === "models" ? <section className="rounded-xl border border-line bg-white" aria-label={t("models.aria")}>
            <SectionHeader icon={<Brain />} title={t("models.title")} note={t("modelCount", { count: modelMetrics.length })} />
            <div className="overflow-x-auto">
              <table className="min-w-[820px] w-full text-left text-[11.5px]">
                <thead className="bg-app-soft text-ink-muted">
                  <tr>
                    <th className="px-3 py-2">{t("th.model")}</th><th className="px-2.5 py-2">{t("th.calls")}</th><th className="px-2.5 py-2">{t("th.inputTokens")}</th>
                    <th className="px-2.5 py-2">{t("th.outputTokens")}</th><th className="px-2.5 py-2">{t("th.toolCalls")}</th><th className="px-2.5 py-2">{t("th.avgTtft")}</th><th className="px-2.5 py-2">{t("th.avgDuration")}</th>
                    <th className="px-2.5 py-2">{t("th.outputRate")}</th><th className="px-2.5 py-2">{t("th.successRate")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {modelMetrics.map((item) => (
                    <tr key={item.model}>
                      <td className="px-3 py-2 font-mono font-bold text-ink">{item.model}</td>
                      <td className="px-2.5 py-2">{item.calls}</td><td className="px-2.5 py-2">{formatEstimatedNumber(item.input, item.estimated)}</td>
                      <td className="px-2.5 py-2">{formatEstimatedNumber(item.output, item.estimated)}</td><td className="px-2.5 py-2">{item.toolCalls}</td><td className="px-2.5 py-2">{formatDuration(item.avgTtft)}</td>
                      <td className="px-2.5 py-2">{formatDuration(item.avgDuration)}</td><td className="px-2.5 py-2">{formatEstimatedRate(item.outputRate, item.estimated)}</td>
                      <td className="px-2.5 py-2">{Math.round(item.successRate * 100)}%</td>
                    </tr>
                  ))}
                  {modelMetrics.length === 0 ? <tr><td colSpan={9} className="px-3 py-8 text-center text-ink-muted">{t("models.empty")}</td></tr> : null}
                </tbody>
              </table>
            </div>
          </section> : null}

          {dimension === "capabilities" ? <section className="rounded-xl border border-line bg-white" aria-label={t("caps.aria")}>
            <SectionHeader icon={<Wrench />} title={t("caps.title")} note={t("logicalCalls", { count: capabilities.reduce((sum, item) => sum + item.calls, 0) })} />
            <div className="grid gap-2 p-2.5 sm:grid-cols-4">
              {(["AINA", "Tool", "Skill", "MCP"] as const).map((type) => (
                <div key={type} className="rounded-lg bg-app-soft px-3 py-2">
                  <div className="text-[10.5px] font-bold text-ink-muted">{type}</div>
                  <div className="mt-1 text-xl font-extrabold text-ink">{capabilityTotals[type]}</div>
                </div>
              ))}
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-[650px] w-full text-left text-[11.5px]">
                <thead className="bg-app-soft text-ink-muted"><tr><th className="px-3 py-2">{t("th.type")}</th><th className="px-2.5 py-2">{t("th.capability")}</th><th className="px-2.5 py-2">{t("th.calls")}</th><th className="px-2.5 py-2">{t("th.attempts")}</th><th className="px-2.5 py-2">{t("th.successRate")}</th><th className="px-2.5 py-2">{t("th.totalDuration")}</th></tr></thead>
                <tbody className="divide-y divide-line">
                  {capabilities.map((item) => (
                    <tr key={`${item.type}-${item.name}`}>
                      <td className="px-3 py-2"><span className="rounded bg-accent-soft px-1.5 py-0.5 font-bold text-accent">{item.type}</span></td>
                      <td className="px-2.5 py-2 font-mono font-bold text-ink">{item.name}</td><td className="px-2.5 py-2">{item.calls}</td>
                      <td className="px-2.5 py-2">{item.attempts}</td><td className="px-2.5 py-2">{Math.round(item.successRate * 100)}%</td><td className="px-2.5 py-2">{formatDuration(item.duration)}</td>
                    </tr>
                  ))}
                  {capabilities.length === 0 ? <tr><td colSpan={6} className="px-3 py-8 text-center text-ink-muted">{t("caps.empty")}</td></tr> : null}
                </tbody>
              </table>
            </div>
            {capabilityTotals.Skill === 0 && capabilityTotals.MCP === 0 ? (
              <p className="px-3 py-2 text-[10.5px] text-ink-subtle">{t("caps.note")}</p>
            ) : null}
          </section> : null}

          {dimension === "errors" ? <section className="rounded-xl border border-line bg-white" aria-label={t("tab.errors")}>
            <SectionHeader icon={<AlertTriangle />} title={t("tab.errors")} note={errors.length ? t("errorCount", { count: errors.length }) : t("conversationHealthy")} />
            <div className="p-3">
              {errors.length ? <div className="space-y-2">{errors.map((item) => (
                <div key={item.id} className="rounded-lg border border-danger-ring bg-danger-soft p-2.5">
                  <div className="flex flex-wrap items-center gap-2"><XCircle className="h-4 w-4 text-danger" /><strong className="text-[12px] text-danger-deep">{item.source}</strong><span className="font-mono text-[10.5px] text-danger">{item.code}</span></div>
                  <p className="mt-1 whitespace-pre-wrap break-words text-[11.5px] text-danger-deep">{item.message}</p>
                  <button
                    type="button"
                    onClick={() => {
                      onFocusRawLog(item.logId, item.traceId);
                      setDimension("logs");
                    }}
                    className="mt-2 inline-flex items-center gap-1 text-[11px] font-bold text-danger-deep hover:underline"
                  >
                    {t("viewRawLog")}<ChevronRight className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}</div> : (
                <div className="flex items-center gap-2 rounded-lg bg-success-soft p-3 text-[12px] font-semibold text-success-deep"><CheckCircle2 className="h-4 w-4" />{t("errors.none")}</div>
              )}
            </div>
          </section> : null}

          {dimension === "spans" ? <section className="rounded-xl border border-line bg-white" aria-label={t("tab.spans")}>
            <SectionHeader icon={<Route />} title={t("tab.spans")} note={t("roundsAndSpans", { rounds: traces.length, spans: spanCount })} />
            <div className="divide-y divide-line">
              {traces.map((trace, index) => (
                <InteractionSpanTree
                  key={trace.trace_id}
                  trace={trace}
                  calls={calls.filter((call) => call.trace_id === trace.trace_id)}
                  round={index + 1}
                />
              ))}
            </div>
          </section> : null}

          {dimension === "logs" ? <RawLogs entries={rawLogs} focusedEntryId={resolvedFocusedLogId} /> : null}
          </div>
        </>
      ) : (
        <div className="rounded-xl bg-white py-14 text-center text-[13px] text-ink-muted">{t("noCallData")}</div>
      )}
    </div>
  );
}

function ConversationOverview({
  conversation,
  traces,
  calls,
}: {
  conversation?: ConversationRecord;
  traces: TraceRecord[];
  calls: LLMCallRecord[];
}) {
  const metrics = buildConversationOverviewMetrics(traces, calls);
  const contextProgress = metrics.contextUsed != null
    ? Math.min(100, (metrics.contextUsed / metrics.contextCapacity) * 100)
    : null;
  return (
    <section className="grid gap-px overflow-hidden rounded-xl bg-line/70 sm:grid-cols-2 lg:grid-cols-4" aria-label={t("overviewAria")}>
      <ConversationMetricGroup
        icon={<Activity />}
        title={t("usage.title")}
        primary={{ label: t("usage.total"), value: metrics.usage.measured ? formatEstimatedNumber(metrics.usage.total, metrics.usage.estimated) : "—" }}
        details={[
          { label: t("th.inputTokens"), value: metrics.usage.measured ? formatEstimatedNumber(metrics.usage.input, metrics.usage.estimated) : "—" },
          { label: t("th.outputTokens"), value: metrics.usage.measured ? formatEstimatedNumber(metrics.usage.output, metrics.usage.estimated) : "—" },
          { label: t("usage.cacheRead"), value: metrics.cacheRead.measured ? formatNumber(metrics.cacheRead.value) : "—", hint: metrics.cacheRead.measured ? t("usage.cacheReadHint") : t("usage.notReported") },
        ]}
      />
      <ConversationMetricGroup
        icon={<Gauge />}
        title={t("rate.title")}
        primary={{ label: t("rate.output"), value: formatEstimatedRate(metrics.outputRate, metrics.rateEstimated) }}
        details={[
          { label: t("rate.total"), value: formatEstimatedRate(metrics.totalRate, metrics.rateEstimated) },
          { label: t("rate.first"), value: formatDuration(metrics.avgTtft), hint: t("rate.ttftHint") },
          { label: t("th.avgDuration"), value: formatDuration(metrics.avgDuration), hint: t("rate.perCallHint") },
        ]}
      />
      <ConversationMetricGroup
        icon={<MessageSquareText />}
        title={t("interaction.title")}
        primary={{ label: t("interaction.rounds"), value: String(traces.length) }}
        details={[
          { label: t("interaction.messages"), value: conversation ? String(conversation.messages.length) : "—", hint: conversation ? undefined : t("interaction.notCollected") },
        ]}
      />
      <ConversationMetricGroup
        icon={<Layers3 />}
        title={t("context.title")}
        primary={{ label: t("context.used"), value: formatEstimatedNumber(metrics.contextUsed, metrics.contextEstimated), hint: metrics.contextUsed == null ? t("usage.notReported") : t("context.usedHint") }}
        details={[
          { label: t("context.capacity"), value: formatNumber(metrics.contextCapacity), hint: t("context.capacityHint") },
          { label: t("context.compressions"), value: String(metrics.compressionCount), hint: t("context.compressionsHint") },
        ]}
        progress={contextProgress}
      />
    </section>
  );
}

function ConversationMetricGroup({
  icon,
  title,
  primary,
  details,
  progress,
}: {
  icon: React.ReactNode;
  title: string;
  primary: { label: string; value: string; hint?: string };
  details: Array<{ label: string; value: string; hint?: string }>;
  progress?: number | null;
}) {
  return (
    <section className="min-w-0 bg-white px-3 py-2.5" aria-label={title}>
      <div className="flex items-center gap-1.5">
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-accent-soft text-accent [&>svg]:h-3 [&>svg]:w-3">{icon}</span>
        <h3 className="truncate text-[10.5px] font-bold text-ink-muted">{title}</h3>
      </div>
      <div className="mt-2" title={primary.hint}>
        <div className="truncate text-[21px] font-extrabold leading-none tracking-tight text-ink">{primary.value}</div>
        <div className="mt-1 text-[9.5px] font-medium text-ink-subtle">{primary.label}</div>
      </div>
      <div className={classNames("mt-2.5 grid gap-2", details.length >= 3 ? "grid-cols-3" : "grid-cols-2")}>
        {details.map((item) => (
          <div key={item.label} className="min-w-0" title={item.hint}>
            <div className="truncate text-[11.5px] font-bold leading-none text-ink">{item.value}</div>
            <div className="mt-1 truncate text-[9px] text-ink-subtle">{item.label}</div>
          </div>
        ))}
      </div>
      {progress != null ? (
        <div className="mt-2 h-1 overflow-hidden rounded-full bg-app-soft" aria-label={t("contextUsedPct", { pct: progress.toFixed(1) })}>
          <div className="h-full rounded-full bg-accent" style={{ width: `${progress}%` }} />
        </div>
      ) : null}
    </section>
  );
}

function InteractionSpanTree({ trace, calls, round }: { trace: TraceRecord; calls: LLMCallRecord[]; round: number }) {
  const rootSpan = findRootSpan(trace);
  const rows = buildSpanRows(trace.spans, trace.root_span_id).filter(({ span }) => span.span_id !== rootSpan?.span_id && span.kind !== "agent");
  const callsBySpanId = new Map(calls.filter((call) => call.span_id).map((call) => [call.span_id, call]));
  const userInput = tracePrompt(trace);
  const finalResponse = traceFinalResponse(trace);
  const usage = interactionTokenUsage(trace, calls);
  return (
    <details className="group p-2.5" aria-label={t("roundTreeAria", { round })}>
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 rounded-lg bg-app-soft px-2.5 py-2">
        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-subtle transition-transform group-open:rotate-90" />
        <strong className="text-[12px] text-ink">{t("roundN", { round })}</strong>
        <TraceStatusDot status={trace.status} />
        <div className="ml-auto flex flex-wrap items-center justify-end gap-x-3 gap-y-1 text-[10.5px] text-ink-subtle" aria-label={t("roundMetricsAria", { round })}>
          <span>{t("in")} <strong className="text-ink-muted">{formatEstimatedNumber(usage.measured ? usage.input : null, usage.estimated)} Token</strong></span>
          <span>{t("out")} <strong className="text-ink-muted">{formatEstimatedNumber(usage.measured ? usage.output : null, usage.estimated)} Token</strong></span>
          <span>{t("latency")} <strong className="text-ink-muted">{formatDuration(rootSpan?.duration_ms ?? traceDuration(trace))}</strong></span>
        </div>
      </summary>
      <div className="mt-2 space-y-1.5">
        <SpanEndpoint role="user" title={t("userInput")} content={userInput ?? t("noUserInput")} />
        {rows.map(({ span, depth }) => (
          <PersonalSpanRow
            key={span.span_id}
            span={span}
            trace={trace}
            modelCall={callsBySpanId.get(span.span_id)}
            finalResponse={finalResponse}
            depth={Math.max(0, depth - 1)}
          />
        ))}
        {rows.length === 0 ? <div className="py-3 text-center text-[11px] text-ink-muted">{t("round.noCalls")}</div> : null}
        {finalResponse ? <SpanEndpoint role="assistant" title={t("finalReply")} content={finalResponse} /> : null}
      </div>
    </details>
  );
}

function SpanEndpoint({ role, title, content }: { role: "user" | "assistant"; title: string; content: string }) {
  const isUser = role === "user";
  return (
    <div className={classNames("flex items-start gap-2.5 rounded-lg px-2.5 py-2", isUser ? "bg-accent-soft/70" : "bg-success-soft/70")}>
      <span className={classNames("flex h-7 w-7 shrink-0 items-center justify-center rounded-lg", isUser ? "bg-accent text-white" : "bg-success-soft text-success-deep")}>
        {isUser ? <MessageSquareText className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
      </span>
      <div className="min-w-0 flex-1">
        <strong className="text-[11.5px] text-ink">{title}</strong>
        <p className="mt-0.5 whitespace-pre-wrap break-words text-[11.5px] leading-relaxed text-ink-muted">{content}</p>
      </div>
    </div>
  );
}

function DimensionTab({ active, onClick, count, children }: { active: boolean; onClick: () => void; count?: number; children: React.ReactNode }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={classNames(
        "inline-flex items-center gap-1.5 px-3 py-2 text-[11.5px] font-bold transition-colors",
        active
          ? "-mb-px rounded-t-lg border-t border-line bg-white text-accent"
          : "rounded-lg text-ink-muted hover:bg-app-soft hover:text-ink",
      )}
    >
      {children}
      {count ? <span className={classNames("rounded px-1.5 py-0.5 text-[9px]", active ? "bg-white/20 text-white" : "bg-danger-soft text-danger")}>{count}</span> : null}
    </button>
  );
}

function MetricCard({ label, value, icon, danger = false }: { label: string; value: string; icon: React.ReactNode; danger?: boolean }) {
  return (
    <div className="flex min-w-0 items-center gap-2.5 px-2.5 py-2">
      <span className={classNames("flex h-7 w-7 shrink-0 items-center justify-center rounded-lg [&>svg]:h-3.5 [&>svg]:w-3.5", danger ? "bg-danger-soft text-danger" : "bg-accent-soft text-accent")}>{icon}</span>
      <div className="min-w-0">
        <div className="text-[17px] font-extrabold leading-none text-ink">{value}</div>
        <div className="mt-0.5 truncate text-[10.5px] font-semibold text-ink-muted">{label}</div>
      </div>
    </div>
  );
}

function SectionHeader({ icon, title, note }: { icon: React.ReactNode; title: string; note: string }) {
  return (
    <div className="flex items-center gap-2 px-3 py-2 [&>svg]:h-4 [&>svg]:w-4 [&>svg]:text-accent">
      {icon}<h2 className="text-[13px] font-extrabold text-ink">{title}</h2><span className="ml-auto text-[10.5px] text-ink-subtle">{note}</span>
    </div>
  );
}

function TraceStatusDot({ status }: { status: TraceRecord["status"] }) {
  if (status === "completed") return <span className="rounded bg-success-soft px-1.5 py-0.5 text-[9px] font-bold text-success-deep">{t("status.done")}</span>;
  if (status === "failed") return <span className="rounded bg-danger-soft px-1.5 py-0.5 text-[9px] font-bold text-danger">{t("status.failed")}</span>;
  return <span className="rounded bg-warning-soft px-1.5 py-0.5 text-[9px] font-bold text-warning-deep">{status === "running" ? t("status.running") : t("status.pendingApproval")}</span>;
}

function PersonalSpanRow({
  span,
  trace,
  modelCall,
  finalResponse,
  depth,
}: {
  span: TraceSpan;
  trace: TraceRecord;
  modelCall?: LLMCallRecord;
  finalResponse: string | null;
  depth: number;
}) {
  const { input, output } = resolveSpanIo(span, trace, modelCall);
  const result = spanTreeResult(span, output, finalResponse);
  return (
    <div aria-label={`${spanKindLabel(span)} ${spanDisplayName(span)}`} className={classNames("rounded-lg bg-app-soft/50 px-2.5 py-2", span.status === "failed" ? "border border-danger-ring" : "")} style={{ marginLeft: Math.min(depth, 6) * 20 }}>
      <div className="flex items-center gap-2">
        <span className="rounded bg-app-soft px-1.5 py-0.5 text-[9.5px] font-bold text-ink-muted">{spanKindLabel(span)}</span>
        <strong className="min-w-0 flex-1 truncate text-[12px] text-ink">{spanDisplayName(span)}</strong>
        {span.kind === "tool" && span.target_version ? <span className="font-mono text-[9.5px] text-ink-subtle">v{span.target_version}</span> : null}
        {span.attempt_no > 1 ? <span className="text-[9.5px] text-warning-deep">{t("attemptN", { n: span.attempt_no })}</span> : null}
        <span className={classNames("rounded px-1.5 py-0.5 text-[9.5px] font-bold", span.status === "completed" ? "bg-success-soft text-success-deep" : span.status === "failed" ? "bg-danger-soft text-danger" : "bg-warning-soft text-warning-deep")}>{spanStatusLabel(span.status)}</span>
        <span className="font-mono text-[10.5px] text-ink-subtle">{formatDuration(span.duration_ms ?? null)}</span>
      </div>
      {span.kind === "tool" ? (
        <div className="mt-2 grid gap-2 md:grid-cols-2">
          <ToolCallPayload label={t("toolArgs")} value={input} emptyText={t("noToolArgs")} />
          <div className="min-w-0 rounded-md bg-white/60 px-2.5 py-2">
            <div className="text-[9.5px] font-bold text-ink-subtle">{t("toolResult")}</div>
            <p className={classNames("mt-1 whitespace-pre-wrap break-words text-[11px] leading-relaxed", span.status === "failed" ? "text-danger-deep" : "text-ink-muted")}>{result}</p>
          </div>
        </div>
      ) : (
        <p className={classNames("mt-1 whitespace-pre-wrap break-words text-[11px] leading-relaxed", span.status === "failed" ? "text-danger-deep" : "text-ink-muted")}>{result}</p>
      )}
    </div>
  );
}

function ToolCallPayload({ label, value, emptyText }: { label: string; value: unknown; emptyText: string }) {
  const record = asRecord(value);
  const facts = readableFacts(record, new Set());
  const text = record ? "" : readableText(value) || (value != null ? readableValue(value) : "");
  return (
    <div className="min-w-0 rounded-md bg-white/60 px-2.5 py-2">
      <div className="text-[9.5px] font-bold text-ink-subtle">{label}</div>
      {facts.length ? (
        <dl className="mt-1 space-y-1 text-[10.5px] leading-relaxed">
          {facts.map((fact) => (
            <div key={fact.label} className="flex min-w-0 gap-1.5">
              <dt className="shrink-0 font-semibold text-ink-muted">{fact.label}：</dt>
              <dd className="min-w-0 break-words text-ink">{fact.value}</dd>
            </div>
          ))}
        </dl>
      ) : <p className="mt-1 break-words text-[10.5px] text-ink-muted">{text || emptyText}</p>}
    </div>
  );
}

function spanStatusLabel(status: TraceSpan["status"]) {
  if (status === "completed") return t("status.ok");
  if (status === "failed") return t("status.failed");
  if (status === "running") return t("status.inProgress");
  if (status === "cancelled") return t("status.cancelled");
  return t("status.pendingApproval");
}

function spanKindLabel(span: TraceSpan) {
  if (span.kind === "tool") return t("th.toolCalls");
  if (span.kind === "model") return t("th.model");
  if (span.kind === "aina") return "AINA";
  return span.kind;
}

function spanDisplayName(span: TraceSpan) {
  return span.kind === "model" ? t("modelOutput") : span.target_id || span.name;
}

function spanTreeResult(span: TraceSpan, output: unknown, finalResponse: string | null) {
  if (span.kind !== "model") return readableEventResult(span, output);
  if (span.status === "failed") return errorField(span.error, "message") || t("err.modelNoResult");
  const response = modelResponseMessage(output);
  const content = readableText(response);
  const toolCalls = asArray(response?.tool_calls);
  if (content && finalResponse && content.trim() === finalResponse.trim()) return t("finalReplyGenerated");
  if (content) return truncateReadableText(content);
  if (toolCalls.length) return t("requestedCalls", { names: toolCallNames(toolCalls).join(t("listSep")) || t("capabilitiesN", { count: toolCalls.length }) });
  return t("modelDoneNoText");
}

function readableFacts(record: Record<string, unknown> | null, omitted: Set<string>) {
  if (!record) return [];
  return Object.entries(record)
    .filter(([key, value]) => !omitted.has(key) && value != null)
    .slice(0, 6)
    .map(([key, value]) => ({ label: readableLabel(key), value: readableValue(value) }));
}

function readableText(value: unknown): string {
  if (typeof value === "string") return value;
  const record = asRecord(value);
  if (!record) return "";
  if (typeof record.content === "string") return record.content;
  if (typeof record.message === "string") return record.message;
  if (record.message != null) return readableText(record.message);
  const firstChoice = asRecord(asArray(record.choices)[0]);
  if (firstChoice?.message != null) return readableText(firstChoice.message);
  if (record.document != null) return readableText(record.document);
  if (record.result != null) return readableText(record.result);
  return "";
}

function readableValue(value: unknown): string {
  if (typeof value === "boolean") return value ? t("yes") : t("no");
  if (typeof value === "number") return formatNumber(value);
  if (typeof value === "string") return truncateReadableText(value.replace(/\s+/g, " "), 120);
  if (Array.isArray(value)) return t("itemsN", { count: value.length });
  const record = asRecord(value);
  if (!record) return t("none");
  const identity = record.name ?? record.id ?? record.status;
  return identity != null ? readableValue(identity) : t("fieldsN", { count: Object.keys(record).length });
}

function readableLabel(key: string) {
  return ({
    activated: t("label.activated"),
    arguments: t("label.arguments"),
    code: t("label.code"),
    content: t("label.content"),
    heading: t("label.heading"),
    iterations: t("label.iterations"),
    language: t("label.language"),
    message_id: t("label.messageId"),
    modified_at: t("label.modifiedAt"),
    name: t("label.name"),
    path: t("label.path"),
    preferred_aina_id: t("label.preferredAina"),
    query: t("label.query"),
    requested_capability: t("label.requestedCapability"),
    result: t("label.result"),
    size_bytes: t("label.sizeBytes"),
    status: t("label.status"),
    url: t("label.url"),
  } as Record<string, string>)[key] ?? key.replaceAll("_", " ");
}

function truncateReadableText(value: string, limit = 480) {
  return value.length > limit ? `${value.slice(0, limit)}…` : value;
}

function resolveSpanIo(span: TraceSpan, trace: TraceRecord, modelCall?: LLMCallRecord) {
  let input = span.input ?? span.attributes.arguments;
  let output = span.output ?? span.attributes.result;

  if (span.kind === "agent") {
    input ??= trace.events.find((event) => event.kind === "user.request")?.details;
    output ??= [...trace.events].reverse().find((event) => event.kind === "final.response")?.details;
  }

  if (span.kind === "model" && modelCall) {
    input ??= modelCall.request;
    output ??= modelCall.response ?? modelCall.error;
  }

  if (span.kind === "model") {
    const iteration = span.attributes.iteration;
    const requested = trace.events.find((event) => event.kind === "model.requested" && event.details.iteration === iteration);
    const completed = trace.events.find((event) => event.kind === "model.completed" && event.details.iteration === iteration);
    input ??= requested?.details;
    output ??= completed?.details;
  }

  if (span.logical_call_id) {
    const relatedEvents = trace.events.filter((event) => event.details.call_id === span.logical_call_id);
    const requested = relatedEvents.find((event) => event.kind.endsWith(".requested"));
    const completed = [...relatedEvents].reverse().find((event) => (
      event.kind.endsWith(".completed") || event.kind === "routing.scope.activated"
    ));
    input ??= requested?.details.arguments ?? requested?.details;
    output ??= completed?.details.result ?? completed?.details;
  }

  if (span.kind === "aina" && output == null && span.attributes.activated != null) {
    output = { activated: span.attributes.activated };
  }

  return { input, output };
}

function RawLogs({ entries, focusedEntryId }: { entries: RawLogEntry[]; focusedEntryId: string | null }) {
  const [role, setRole] = useState<"all" | LogRole>("all");
  const visible = role === "all" ? entries : entries.filter((entry) => entry.role === role);
  useEffect(() => {
    if (!focusedEntryId) return;
    setRole("all");
    const frame = window.requestAnimationFrame(() => {
      const target = document.getElementById(`raw-log-error-${focusedEntryId}`)
        ?? document.getElementById(`raw-log-${focusedEntryId}`);
      target?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [focusedEntryId]);
  return (
    <section className="rounded-xl border border-line bg-white" aria-label={t("tab.logs")}>
      <SectionHeader icon={<TerminalSquare />} title={t("tab.logs")} note={t("fullIo", { count: visible.length })} />
      <div className="flex flex-wrap gap-1.5 p-2.5" aria-label={t("logs.roleFilterAria")}>
        {(["all", "user", "assistant", "tool", "system"] as const).map((item) => (
          <button key={item} type="button" onClick={() => setRole(item)} className={classNames("rounded-md px-2.5 py-1 text-[10.5px] font-bold", role === item ? "bg-accent text-white" : "bg-app-soft text-ink-muted")}>
            {item === "all" ? t("all") : item}
          </button>
        ))}
      </div>
      <div className="space-y-2 p-3">
        {visible.map((entry) => (
          <RawLogItem key={entry.id} entry={entry} focused={entry.id === focusedEntryId} />
        ))}
        {visible.length === 0 ? <div className="py-8 text-center text-[12px] text-ink-muted">{t("logs.emptyRole")}</div> : null}
      </div>
    </section>
  );
}

function RawLogItem({ entry, focused }: { entry: RawLogEntry; focused: boolean }) {
  const [view, setView] = useState<"input" | "output">("input");
  const [fullLogState, setFullLogState] = useState<"idle" | "loading" | "loaded" | "empty">("idle");
  const [fullLogDetail, setFullLogDetail] = useState<unknown>(null);
  const hasInput = entry.input != null;
  const hasOutput = entry.output != null;
  const showSwitch = hasInput && hasOutput;
  const loadFullLog = async () => {
    if (!entry.rawLog) return;
    setFullLogState("loading");
    try {
      const result = await getRawLogs(entry.rawLog.traceId, entry.rawLog.spanId);
      if (result?.status === "ready" && result.detail != null) {
        setFullLogDetail(result.detail);
        setFullLogState("loaded");
      } else {
        setFullLogState("empty");
      }
    } catch {
      setFullLogState("empty");
    }
  };
  return (
    <details
      id={`raw-log-${entry.id}`}
      open={focused ? true : undefined}
      aria-label={t("rawLogOf", { id: entry.id })}
      className={classNames(
        "overflow-hidden rounded-lg bg-app-soft/50",
        focused && "ring-2 ring-accent/50",
      )}
    >
      <summary className="flex cursor-pointer list-none items-center gap-2 bg-white/40 px-2.5 py-2">
        <span className="rounded bg-white px-1.5 py-0.5 font-mono text-[9.5px] font-bold text-accent">{entry.role}</span>
        <strong className="min-w-0 flex-1 truncate text-[11.5px] text-ink">{entry.title}</strong>
        <span className="truncate text-[10.5px] text-ink-subtle">{entry.subtitle}</span>
        <ChevronRight className="h-3.5 w-3.5 text-ink-subtle" />
      </summary>
      <div className="space-y-2.5 p-2.5">
        {showSwitch ? (
          <div className="flex w-fit rounded-lg bg-slate-900 p-0.5">
            <button
              type="button"
              onClick={() => setView("input")}
              className={classNames("rounded-md px-2.5 py-1 text-[10.5px] font-bold transition-colors", view === "input" ? "bg-white text-slate-900" : "text-slate-400 hover:text-white")}
            >
              {t("io.input")}
            </button>
            <button
              type="button"
              onClick={() => setView("output")}
              className={classNames("rounded-md px-2.5 py-1 text-[10.5px] font-bold transition-colors", view === "output" ? "bg-white text-slate-900" : "text-slate-400 hover:text-white")}
            >
              {t("io.output")}
            </button>
          </div>
        ) : null}
        {view === "input" && hasInput ? <JsonPayload label={t("io.rawInput")} value={entry.input} /> : null}
        {view === "output" && hasOutput ? <JsonPayload label={t("io.rawOutput")} value={entry.output} /> : null}
        {entry.error != null ? (
          <div
            id={focused ? `raw-log-error-${entry.id}` : undefined}
            className={classNames("rounded-md", focused && "ring-2 ring-danger/60")}
          >
            <JsonPayload label={t("io.error")} value={entry.error} danger />
          </div>
        ) : null}
        {entry.rawLog ? (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={loadFullLog}
              disabled={fullLogState === "loading"}
              className="inline-flex items-center gap-1 rounded-md bg-slate-900 px-2.5 py-1 text-[10.5px] font-bold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {fullLogState === "loading" ? t("loading") : t("logs.viewFull")}
            </button>
            {fullLogState === "loaded" ? <JsonPayload label={t("logs.full")} value={fullLogDetail} /> : null}
            {fullLogState === "empty" ? <span className="text-[10.5px] text-ink-subtle">{t("logs.fullEmpty")}</span> : null}
          </div>
        ) : null}
        {!hasInput && !hasOutput && entry.error == null ? <div className="py-4 text-center text-[11px] text-ink-subtle">{t("logs.noIo")}</div> : null}
      </div>
    </details>
  );
}

function JsonPayload({ label, value, className, danger = false }: { label?: string; value: unknown; className?: string; danger?: boolean }) {
  return (
    <section>
      {label ? <div className={classNames("mb-1 text-[10.5px] font-bold", danger ? "text-danger" : "text-ink-muted")}>{label}</div> : null}
      <pre className={classNames("overflow-auto whitespace-pre-wrap break-all rounded-md p-3 font-mono text-[10.5px] leading-relaxed", danger ? "bg-danger-soft text-danger-deep" : "bg-slate-950 text-slate-200", className)}><JsonSyntax value={value} /></pre>
    </section>
  );
}

function JsonSyntax({ value, depth = 0 }: { value: unknown; depth?: number }) {
  const pad = (level: number) => "  ".repeat(level);
  if (value === null) return <span className="text-rose-400">null</span>;
  if (typeof value === "string") return <span className="text-emerald-400">{JSON.stringify(value)}</span>;
  if (typeof value === "number") return <span className="text-amber-400">{String(value)}</span>;
  if (typeof value === "boolean") return <span className="text-violet-400">{String(value)}</span>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-slate-500">[]</span>;
    return (
      <>
        <span className="text-slate-500">[</span>
        {"\n"}
        {value.map((item, index) => (
          <span key={index}>
            {pad(depth + 1)}
            <JsonSyntax value={item} depth={depth + 1} />
            {index < value.length - 1 ? <span className="text-slate-500">,</span> : null}
            {"\n"}
          </span>
        ))}
        {pad(depth)}<span className="text-slate-500">]</span>
      </>
    );
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return <span className="text-slate-500">{"{}"}</span>;
    return (
      <>
        <span className="text-slate-500">{"{"}</span>
        {"\n"}
        {entries.map(([key, item], index) => (
          <span key={key}>
            {pad(depth + 1)}
            <span className="text-sky-400">{JSON.stringify(key)}</span>
            <span className="text-slate-500">: </span>
            <JsonSyntax value={item} depth={depth + 1} />
            {index < entries.length - 1 ? <span className="text-slate-500">,</span> : null}
            {"\n"}
          </span>
        ))}
        {pad(depth)}<span className="text-slate-500">{"}"}</span>
      </>
    );
  }
  return <span className="text-slate-400">{String(value)}</span>;
}

function PersonalOverview({
  traces,
  calls,
  overview,
  onPeriodChange,
}: {
  traces: TraceRecord[];
  calls: LLMCallRecord[];
  overview?: ObsOverview | null;
  onPeriodChange?: (period: Period) => void;
}) {
  const [period, setPeriod] = useState<Period>("month");
  const switchPeriod = (next: Period) => {
    setPeriod(next);
    onPeriodChange?.(next);
  };
  const samples = useMemo(() => buildTokenSamples(traces, calls), [calls, traces]);
  const range = periodRange(period);

  // 后端聚合 DTO 优先：个人总览不再由前端加载全量 LLM Call 计算（design 12.2）
  if (overview) {
    const totals = {
      input: overview.input_tokens,
      output: overview.output_tokens,
      total: overview.total_tokens,
    };
    const byModel = overview.per_model.map((row) => ({
      model: row.model,
      calls: row.call_count,
      input: row.input_tokens,
      output: row.output_tokens,
      total: row.input_tokens + row.output_tokens,
    }));
    const dailySamples: TokenSample[] = overview.daily.map((row) => ({
      id: row.day ?? "unknown",
      model: "",
      conversationId: null,
      createdAt: row.day ?? new Date().toISOString(),
      input: row.input_tokens,
      output: row.output_tokens,
      total: row.input_tokens + row.output_tokens,
      measured: true,
      estimated: false,
    }));
    return (
      <div className="space-y-2.5">
        <section className="px-1 py-1" aria-label={t("range.aria")}>
          <div className="flex flex-wrap items-center gap-2">
            <CalendarDays className="h-4 w-4 text-accent" />
            <strong className="mr-2 text-[12px] text-ink">{t("range.period")}</strong>
            {(["day", "week", "month"] as const).map((item) => (
              <button key={item} type="button" onClick={() => switchPeriod(item)} className={classNames("rounded-md px-2.5 py-1 text-[11px] font-bold", period === item ? "bg-accent text-white" : "bg-app-soft text-ink-muted")}>{periodLabel(item)}</button>
            ))}
            {overview.start && overview.end ? (
              <span className="ml-auto text-[10.5px] text-ink-subtle">{formatDate(new Date(overview.start))} — {formatDate(new Date(overview.end))}</span>
            ) : null}
          </div>
        </section>

        <section className="grid grid-cols-2 overflow-hidden rounded-xl bg-white p-1 md:grid-cols-3 xl:grid-cols-6" aria-label={t("personal.aria")}>
          <MetricCard label={t("personal.total")} value={formatNumber(totals.total)} icon={<Activity />} />
          <MetricCard label={t("th.inputTokens")} value={formatNumber(totals.input)} icon={<Brain />} />
          <MetricCard label={t("th.outputTokens")} value={formatNumber(totals.output)} icon={<Sparkles />} />
          <MetricCard label={t("personal.conversations")} value={String(overview.conversation_count)} icon={<MessageSquareText />} />
          <MetricCard label={t("interaction.rounds")} value={String(overview.trace_count)} icon={<Route />} />
          <MetricCard label={t("personal.activeDays")} value={String(overview.active_days)} icon={<CalendarDays />} />
        </section>

        <section className="rounded-xl bg-white" aria-label={t("byModel.title")}>
          <SectionHeader icon={<Brain />} title={t("byModel.title")} note={t("modelCount", { count: byModel.length })} />
          <div className="overflow-x-auto">
            <table className="min-w-[620px] w-full text-left text-[11.5px]">
              <thead className="bg-app-soft text-ink-muted"><tr><th className="px-3 py-2">{t("th.model")}</th><th className="px-2.5 py-2">{t("th.calls")}</th><th className="px-2.5 py-2">{t("io.input")}</th><th className="px-2.5 py-2">{t("io.output")}</th><th className="px-2.5 py-2">{t("personal.total")}</th><th className="px-2.5 py-2">{t("th.share")}</th></tr></thead>
              <tbody className="divide-y divide-line">
                {byModel.map((item) => (
                  <tr key={item.model}><td className="px-3 py-2 font-mono font-bold text-ink">{item.model}</td><td className="px-2.5 py-2">{item.calls}</td><td className="px-2.5 py-2">{formatNumber(item.input)}</td><td className="px-2.5 py-2">{formatNumber(item.output)}</td><td className="px-2.5 py-2 font-bold text-ink">{formatNumber(item.total)}</td><td className="px-2.5 py-2">{totals.total ? `${((item.total / totals.total) * 100).toFixed(1)}%` : "0%"}</td></tr>
                ))}
                {byModel.length === 0 ? <tr><td colSpan={6} className="px-3 py-8 text-center text-ink-muted">{t("byModel.empty")}</td></tr> : null}
              </tbody>
            </table>
          </div>
        </section>

        <TokenCalendar samples={dailySamples} />
      </div>
    );
  }

  const periodSamples = samples.filter((sample) => inRange(sample.createdAt, range.start, range.end));
  const periodTraces = traces.filter((trace) => inRange(trace.created_at, range.start, range.end));
  const totals = periodSamples.reduce((sum, sample) => ({ input: sum.input + sample.input, output: sum.output + sample.output, total: sum.total + sample.total }), { input: 0, output: 0, total: 0 });
  const activeDays = new Set([...periodTraces.map((trace) => toDateKey(new Date(trace.created_at))), ...periodSamples.map((sample) => toDateKey(new Date(sample.createdAt)))]).size;
  const conversationCount = new Set([...periodTraces.map((trace) => trace.conversation_id), ...periodSamples.map((sample) => sample.conversationId)].filter(Boolean)).size;
  const byModel = groupSamplesByModel(periodSamples);

  return (
    <div className="space-y-2.5">
      <section className="px-1 py-1" aria-label={t("range.aria")}>
        <div className="flex flex-wrap items-center gap-2">
          <CalendarDays className="h-4 w-4 text-accent" />
          <strong className="mr-2 text-[12px] text-ink">{t("range.period")}</strong>
          {(["day", "week", "month"] as const).map((item) => (
            <button key={item} type="button" onClick={() => setPeriod(item)} className={classNames("rounded-md px-2.5 py-1 text-[11px] font-bold", period === item ? "bg-accent text-white" : "bg-app-soft text-ink-muted")}>{periodLabel(item)}</button>
          ))}
          <span className="ml-auto text-[10.5px] text-ink-subtle">{formatDate(range.start)} — {formatDate(range.end)}</span>
        </div>
      </section>

      <section className="grid grid-cols-2 overflow-hidden rounded-xl bg-white p-1 md:grid-cols-3 xl:grid-cols-6" aria-label={t("personal.aria")}>
        <MetricCard label={t("personal.total")} value={formatNumber(totals.total)} icon={<Activity />} />
        <MetricCard label={t("th.inputTokens")} value={formatNumber(totals.input)} icon={<Brain />} />
        <MetricCard label={t("th.outputTokens")} value={formatNumber(totals.output)} icon={<Sparkles />} />
        <MetricCard label={t("personal.conversations")} value={String(conversationCount)} icon={<MessageSquareText />} />
        <MetricCard label={t("interaction.rounds")} value={String(periodTraces.length)} icon={<Route />} />
        <MetricCard label={t("personal.activeDays")} value={String(activeDays)} icon={<CalendarDays />} />
      </section>

      <section className="rounded-xl bg-white" aria-label={t("byModel.title")}>
        <SectionHeader icon={<Brain />} title={t("byModel.title")} note={t("modelCount", { count: byModel.length })} />
        <div className="overflow-x-auto">
          <table className="min-w-[620px] w-full text-left text-[11.5px]">
            <thead className="bg-app-soft text-ink-muted"><tr><th className="px-3 py-2">{t("th.model")}</th><th className="px-2.5 py-2">{t("th.calls")}</th><th className="px-2.5 py-2">{t("io.input")}</th><th className="px-2.5 py-2">{t("io.output")}</th><th className="px-2.5 py-2">{t("personal.total")}</th><th className="px-2.5 py-2">{t("th.share")}</th></tr></thead>
            <tbody className="divide-y divide-line">
              {byModel.map((item) => (
                <tr key={item.model}><td className="px-3 py-2 font-mono font-bold text-ink">{item.model}</td><td className="px-2.5 py-2">{item.calls}</td><td className="px-2.5 py-2">{formatNumber(item.input)}</td><td className="px-2.5 py-2">{formatNumber(item.output)}</td><td className="px-2.5 py-2 font-bold text-ink">{formatNumber(item.total)}</td><td className="px-2.5 py-2">{totals.total ? `${((item.total / totals.total) * 100).toFixed(1)}%` : "0%"}</td></tr>
              ))}
              {byModel.length === 0 ? <tr><td colSpan={6} className="px-3 py-8 text-center text-ink-muted">{t("byModel.empty")}</td></tr> : null}
            </tbody>
          </table>
        </div>
      </section>

      <TokenCalendar samples={samples} />
    </div>
  );
}

function TokenCalendar({ samples }: { samples: TokenSample[] }) {
  const end = startOfDay(new Date());
  const start = addDays(end, -364);
  const first = addDays(start, -start.getDay());
  const last = addDays(end, 6 - end.getDay());
  const values = new Map<string, number>();
  for (const sample of samples) {
    const key = toDateKey(new Date(sample.createdAt));
    values.set(key, (values.get(key) ?? 0) + sample.total);
  }
  const cells: Date[] = [];
  for (let date = first; date <= last; date = addDays(date, 1)) cells.push(date);
  const max = Math.max(0, ...values.values());
  return (
    <section className="rounded-xl bg-white" aria-label={t("calendar.title")}>
      <SectionHeader icon={<CalendarDays />} title={t("calendar.title")} note={t("calendar.note")} />
      <div className="overflow-x-auto p-3">
        <div className="min-w-[760px]">
          <div className="mb-2 flex justify-between text-[9.5px] text-ink-subtle"><span>{formatMonth(first)}</span><span>{formatMonth(addDays(first, 91))}</span><span>{formatMonth(addDays(first, 182))}</span><span>{formatMonth(addDays(first, 273))}</span><span>{formatMonth(last)}</span></div>
          <div className="grid grid-flow-col grid-rows-7 gap-[3px]" aria-label={t("calendar.heatmapAria")}>
            {cells.map((date) => {
              const key = toDateKey(date);
              const value = values.get(key) ?? 0;
              const outside = date < start || date > end;
              return <span key={key} title={t("heatTitle", { key, value: formatNumber(value) })} aria-label={`${key} ${value} Token`} className={classNames("aspect-square min-w-[10px] rounded-[2px]", outside ? "bg-transparent" : heatColor(value, max))} />;
            })}
          </div>
          <div className="mt-3 flex items-center justify-end gap-1 text-[9.5px] text-ink-subtle"><span className="mr-1">{t("calendar.less")}</span>{["bg-slate-100", "bg-emerald-100", "bg-emerald-300", "bg-emerald-500", "bg-emerald-700"].map((color) => <span key={color} className={classNames("h-3 w-3 rounded-[2px]", color)} />)}<span className="ml-1">{t("calendar.more")}</span></div>
        </div>
      </div>
    </section>
  );
}

function callTokenUsage(call: LLMCallRecord): TokenUsage {
  const usage = asRecord(call.response?.usage);
  const input = numberValue(usage?.prompt_tokens ?? usage?.input_tokens);
  const output = numberValue(usage?.completion_tokens ?? usage?.output_tokens);
  const reportedTotal = numberValue(usage?.total_tokens);
  const measured = input != null || output != null || reportedTotal != null;
  const total = reportedTotal ?? (input ?? 0) + (output ?? 0);
  return { input: input ?? 0, output: output ?? 0, total, measured, estimated: usage?.estimated === true || usage?.source === "estimated" };
}

function spanTokenUsage(span: TraceSpan): TokenUsage {
  const input = numberValue(span.attributes.input_tokens);
  const output = numberValue(span.attributes.output_tokens);
  return { input: input ?? 0, output: output ?? 0, total: (input ?? 0) + (output ?? 0), measured: input != null || output != null, estimated: span.attributes.usage_estimated === true };
}

function sumTokenUsage(usages: TokenUsage[]): TokenUsage {
  return usages.reduce((sum, usage) => ({ input: sum.input + usage.input, output: sum.output + usage.output, total: sum.total + usage.total, measured: sum.measured || usage.measured, estimated: sum.estimated || usage.estimated }), { input: 0, output: 0, total: 0, measured: false, estimated: false });
}

function buildConversationOverviewMetrics(traces: TraceRecord[], calls: LLMCallRecord[]) {
  const usage = sumTokenUsage(buildTokenSamples(traces, calls));
  const cacheReadValues = calls.map(callCacheReadTokens).filter((value): value is number => value != null);
  const spansById = new Map(traces.flatMap((trace) => trace.spans).map((span) => [span.span_id, span]));
  const timings = calls.map((call) => {
    const span = call.span_id ? spansById.get(call.span_id) : undefined;
    return {
      duration: numberValue(call.duration_ms) ?? numberValue(span?.duration_ms),
      ttft: numberValue(call.ttft_ms) ?? numberValue(span?.attributes.ttft_ms),
      usage: callTokenUsage(call),
    };
  });
  const recordedSpanIds = new Set(calls.map((call) => call.span_id).filter((id): id is string => Boolean(id)));
  const tracesWithMeasuredCalls = new Set(calls.filter((call) => call.trace_id && callTokenUsage(call).measured).map((call) => call.trace_id as string));
  for (const trace of traces) {
    if (tracesWithMeasuredCalls.has(trace.trace_id)) continue;
    trace.spans.filter((span) => span.kind === "model" && !recordedSpanIds.has(span.span_id)).forEach((span) => timings.push({
      duration: numberValue(span.duration_ms),
      ttft: numberValue(span.attributes.ttft_ms),
      usage: spanTokenUsage(span),
    }));
  }
  const durations = timings.map((item) => item.duration).filter((value): value is number => value != null);
  const ttfts = timings.map((item) => item.ttft).filter((value): value is number => value != null);
  const rateTimings = timings.filter((item) => item.duration != null && item.usage.measured);
  const rateDuration = rateTimings.reduce((sum, item) => sum + (item.duration ?? 0), 0);
  const rateUsage = sumTokenUsage(rateTimings.map((item) => item.usage));
  const latestCall = [...calls].sort((left, right) => right.created_at.localeCompare(left.created_at))[0];
  const latestModelSpan = traces.flatMap((trace) => trace.spans).filter((span) => span.kind === "model").sort((left, right) => right.started_at.localeCompare(left.started_at))[0];
  const latestSpanUsage = latestModelSpan ? spanTokenUsage(latestModelSpan) : null;
  const latestCallUsage = latestCall ? callTokenUsage(latestCall) : null;
  const latestCallInput = latestCall ? callInputTokens(latestCall) : null;
  const contextUsed = latestCallInput ?? (latestSpanUsage?.measured ? latestSpanUsage.input : null);
  const compressionEvents = traces.flatMap((trace) => trace.events).filter((event) => event.status === "completed" && isContextCompression(event.kind)).length;
  const compressionSpans = traces.flatMap((trace) => trace.spans).filter((span) => span.kind === "internal" && span.status === "completed" && isContextCompression(span.name)).length;
  return {
    usage,
    cacheRead: { value: cacheReadValues.reduce((sum, value) => sum + value, 0), measured: cacheReadValues.length > 0 },
    totalRate: rateDuration > 0 && rateUsage.measured ? rateUsage.total / (rateDuration / 1000) : null,
    outputRate: rateDuration > 0 && rateUsage.measured ? rateUsage.output / (rateDuration / 1000) : null,
    rateEstimated: rateUsage.estimated,
    avgTtft: ttfts.length ? ttfts.reduce((sum, value) => sum + value, 0) / ttfts.length : null,
    avgDuration: durations.length ? durations.reduce((sum, value) => sum + value, 0) / durations.length : null,
    contextUsed,
    contextEstimated: latestCallInput != null ? (latestCallUsage?.estimated || !latestCallUsage?.measured) : (latestSpanUsage?.estimated ?? false),
    contextCapacity: (latestCall ? callContextCapacity(latestCall) : null) ?? DEFAULT_CONTEXT_CAPACITY,
    compressionCount: compressionEvents || compressionSpans,
  };
}

function callInputTokens(call: LLMCallRecord) {
  const usage = asRecord(call.response?.usage);
  return numberValue(usage?.prompt_tokens ?? usage?.input_tokens ?? call.request.estimated_prompt_tokens);
}

function callCacheReadTokens(call: LLMCallRecord) {
  const usage = asRecord(call.response?.usage);
  const promptDetails = asRecord(usage?.prompt_tokens_details);
  const inputDetails = asRecord(usage?.input_tokens_details);
  return numberValue(promptDetails?.cached_tokens ?? inputDetails?.cached_tokens ?? usage?.cache_read_input_tokens ?? usage?.cached_tokens);
}

function callContextCapacity(call: LLMCallRecord) {
  const response = asRecord(call.response);
  const usage = asRecord(response?.usage);
  const requestMetadata = asRecord(call.request.metadata);
  const responseMetadata = asRecord(response?.metadata);
  const candidates = [
    call.request.context_window,
    call.request.context_length,
    call.request.max_context_tokens,
    call.request.model_context_window,
    requestMetadata?.context_window,
    response?.context_window,
    response?.model_context_window,
    responseMetadata?.context_window,
    usage?.context_window,
  ];
  return candidates.map(numberValue).find((value) => value != null && value > 0) ?? null;
}

function isContextCompression(value: string) {
  const normalized = value.toLowerCase();
  return normalized.includes("compress") || normalized.includes("compact");
}

function buildModelMetrics(traces: TraceRecord[], calls: LLMCallRecord[]) {
  const modelSpans = traces.flatMap((trace) => trace.spans).filter((span) => span.kind === "model");
  const spansById = new Map(modelSpans.map((span) => [span.span_id, span]));
  const records = calls.map((call) => {
    const span = call.span_id ? spansById.get(call.span_id) : undefined;
    const usage = callTokenUsage(call);
    return { model: call.model || span?.target_id || t("unknownModel"), input: usage.input, output: usage.output, estimated: usage.estimated, toolCalls: modelToolCallCount(call.response, span), duration: call.duration_ms ?? span?.duration_ms ?? 0, ttft: call.ttft_ms ?? numberValue(span?.attributes.ttft_ms) ?? null, success: call.status === "completed" };
  });
  const recordedSpanIds = new Set(calls.map((call) => call.span_id).filter(Boolean));
  for (const span of modelSpans.filter((item) => !recordedSpanIds.has(item.span_id))) {
    const usage = spanTokenUsage(span);
    records.push({ model: span.target_id || span.name, input: usage.input, output: usage.output, estimated: usage.estimated, toolCalls: modelToolCallCount(span.output, span), duration: span.duration_ms ?? 0, ttft: numberValue(span.attributes.ttft_ms), success: span.status === "completed" });
  }
  const grouped = new Map<string, typeof records>();
  for (const record of records) grouped.set(record.model, [...(grouped.get(record.model) ?? []), record]);
  return [...grouped.entries()].map(([model, items]) => {
    const input = items.reduce((sum, item) => sum + item.input, 0);
    const output = items.reduce((sum, item) => sum + item.output, 0);
    const duration = items.reduce((sum, item) => sum + item.duration, 0);
    const ttfts = items.map((item) => item.ttft).filter((value): value is number => value != null);
    return { model, calls: items.length, input, output, estimated: items.some((item) => item.estimated), toolCalls: items.reduce((sum, item) => sum + item.toolCalls, 0), avgDuration: items.length ? duration / items.length : null, avgTtft: ttfts.length ? ttfts.reduce((sum, value) => sum + value, 0) / ttfts.length : null, outputRate: duration > 0 ? output / (duration / 1000) : null, successRate: items.length ? items.filter((item) => item.success).length / items.length : 0 };
  });
}

function modelToolCallCount(responseValue: unknown, span?: TraceSpan) {
  const recorded = numberValue(span?.attributes.tool_call_count);
  if (recorded != null) return recorded;
  const response = asRecord(responseValue);
  const message = asRecord(response?.message);
  const firstChoice = asRecord(asArray(response?.choices)[0]);
  const choiceMessage = asRecord(firstChoice?.message);
  const toolCalls = response?.tool_calls ?? message?.tool_calls ?? choiceMessage?.tool_calls;
  return asArray(toolCalls).length;
}

function capabilityMetrics(spans: TraceSpan[]) {
  const relevant = spans.filter((span) => span.kind === "tool" || span.kind === "aina");
  const grouped = new Map<string, TraceSpan[]>();
  for (const span of relevant) {
    const type = capabilityType(span);
    const name = span.target_id || span.name;
    const key = `${type}:${name}`;
    grouped.set(key, [...(grouped.get(key) ?? []), span]);
  }
  return [...grouped.values()].map((items) => {
    const first = items[0];
    const logicalCalls = new Set(items.map((span) => span.logical_call_id || span.span_id));
    return { type: capabilityType(first), name: first.target_id || first.name, calls: logicalCalls.size, attempts: items.length, duration: items.reduce((sum, span) => sum + (span.duration_ms ?? 0), 0), successRate: items.length ? items.filter((span) => span.status === "completed").length / items.length : 0 };
  });
}

function capabilityType(span: TraceSpan): "AINA" | "Tool" | "Skill" | "MCP" {
  if (span.kind === "aina") return "AINA";
  const hints = [span.attributes.capability_kind, span.attributes.transport, span.attributes.protocol, span.attributes.source, span.target_id, span.name]
    .filter((value): value is string => typeof value === "string")
    .join(" ").toLowerCase();
  if (hints.includes("skill")) return "Skill";
  if (hints.includes("mcp")) return "MCP";
  return "Tool";
}

function countCapabilityTypes(items: ReturnType<typeof capabilityMetrics>) {
  return items.reduce((sum, item) => ({ ...sum, [item.type]: sum[item.type] + item.calls }), { AINA: 0, Tool: 0, Skill: 0, MCP: 0 });
}

function buildErrors(trace: TraceRecord, calls: LLMCallRecord[]): DiagnosticError[] {
  const errors: DiagnosticError[] = [];
  const callSpanIds = new Set(calls.map((call) => call.span_id).filter(Boolean));
  const failedSpans = trace.spans.filter((span) => (
    span.kind !== "agent"
    && (span.status === "failed" || span.error)
    && !callSpanIds.has(span.span_id)
  ));
  for (const span of failedSpans) {
    errors.push({
      id: span.span_id,
      traceId: trace.trace_id,
      logId: span.kind === "model" ? `${span.span_id}-model` : span.span_id,
      source: `${span.kind} · ${span.target_id || span.name}`,
      code: errorField(span.error, "code") || span.status.toUpperCase(),
      message: errorField(span.error, "message") || stringifyError(span.error) || t("err.callFailedNoDetail"),
    });
  }
  for (const call of calls.filter((item) => item.status === "failed" || item.error)) {
    errors.push({
      id: call.call_id,
      traceId: trace.trace_id,
      logId: call.call_id,
      source: `model · ${call.model}`,
      code: call.status.toUpperCase(),
      message: call.error || t("err.modelFailedNoDetail"),
    });
  }
  if (!errors.length && trace.status === "failed") {
    const root = findRootSpan(trace);
    errors.push({
      id: trace.trace_id,
      traceId: trace.trace_id,
      logId: root ? `${root.span_id}-user` : trace.trace_id,
      source: t("err.sourceAgent"),
      code: "TRACE_FAILED",
      message: t("err.interactionFailed"),
    });
  }
  return errors;
}

function readableEventResult(span: TraceSpan, output: unknown) {
  if (span.status === "failed") return errorField(span.error, "message") || t("err.callNoResult");
  const record = asRecord(output);
  const document = asRecord(record?.document);
  if (document) {
    const name = typeof document.name === "string" ? document.name : t("document");
    const size = numberValue(document.size_bytes);
    return t("returnedDoc", { name, size: size != null ? t("bytesSuffix", { value: formatNumber(size) }) : "" });
  }
  if (span.kind === "aina" && (record?.activated === true || span.attributes.activated === true)) return t("ainaActivated");
  const text = readableText(output);
  if (text) return truncateReadableText(text);
  const facts = readableFacts(record, new Set(["content", "result", "document"]));
  return facts.length ? facts.map((fact) => t("fact", { label: fact.label, value: fact.value })).join(" · ") : t("callDone");
}

function modelResponseMessage(output: unknown) {
  const record = asRecord(output);
  const firstChoice = asRecord(asArray(record?.choices)[0]);
  return asRecord(firstChoice?.message) ?? record;
}

function toolCallNames(toolCalls: unknown[]) {
  return toolCalls
    .map((call) => asRecord(asRecord(call)?.function)?.name)
    .filter((name): name is string => typeof name === "string");
}

function spanRawLogRef(span?: TraceSpan): { traceId: string; spanId: string } | undefined {
  const traceId = span?.attributes?.otel_trace_id;
  const spanId = span?.attributes?.otel_span_id;
  if (typeof traceId === "string" && typeof spanId === "string" && traceId && spanId) {
    return { traceId, spanId };
  }
  return undefined;
}

function buildRawLogs(trace: TraceRecord, calls: LLMCallRecord[]): RawLogEntry[] {
  const root = findRootSpan(trace);
  const logs: RawLogEntry[] = [];
  if (root) {
    const rootIo = resolveSpanIo(root, trace);
    logs.push({ id: `${root.span_id}-user`, traceId: trace.trace_id, role: "user", title: t("log.interactionInput"), subtitle: root.span_id, input: rootIo.input, error: root.error ?? undefined, rawLog: spanRawLogRef(root) });
  }
  calls.forEach((call, index) => {
    const requestMessages = asArray(call.request.messages);
    const ownerSpan = trace.spans.find((span) => span.attributes?.otel_span_id === call.span_id || span.span_id === call.span_id);
    requestMessages.forEach((message, messageIndex) => {
      const record = asRecord(message);
      const role = normalizeRole(record?.role);
      if (role === "system") logs.push({ id: `${call.call_id}-context-${messageIndex}`, traceId: trace.trace_id, role, title: t("modelContext", { role }), subtitle: call.model, input: message, rawLog: spanRawLogRef(ownerSpan) });
    });
    logs.push({ id: call.call_id, traceId: trace.trace_id, role: "assistant", title: t("modelRequestN", { n: index + 1, model: call.model }), subtitle: call.call_id, input: call.request, output: call.response ?? undefined, error: call.error ?? undefined, rawLog: spanRawLogRef(ownerSpan) });
  });
  const callSpanIds = new Set(calls.map((call) => call.span_id).filter(Boolean));
  trace.spans.filter((span) => span.kind === "model" && !callSpanIds.has(span.span_id)).forEach((span, index) => {
    const io = resolveSpanIo(span, trace);
    logs.push({
      id: `${span.span_id}-model`,
      traceId: trace.trace_id,
      role: "assistant",
      title: t("modelSpanN", { n: index + 1, name: span.target_id || span.name }),
      subtitle: span.span_id,
      input: io.input,
      output: io.output,
      error: span.error ?? undefined,
      rawLog: spanRawLogRef(span),
    });
  });
  trace.spans.filter((span) => span.kind === "tool" || span.kind === "aina").forEach((span) => {
    const io = resolveSpanIo(span, trace);
    logs.push({
      id: span.span_id,
      traceId: trace.trace_id,
      role: "tool",
      title: `${capabilityType(span)} · ${span.target_id || span.name}`,
      subtitle: span.logical_call_id || span.span_id,
      input: io.input,
      output: io.output,
      error: span.error ?? undefined,
      rawLog: spanRawLogRef(span),
    });
  });
  trace.spans.filter((span) => span.kind === "internal" && (span.status === "failed" || span.error)).forEach((span) => {
    const io = resolveSpanIo(span, trace);
    logs.push({
      id: span.span_id,
      traceId: trace.trace_id,
      role: "system",
      title: t("internalCall", { name: span.target_id || span.name }),
      subtitle: span.span_id,
      input: io.input,
      output: io.output,
      error: span.error ?? undefined,
      rawLog: spanRawLogRef(span),
    });
  });
  return logs;
}

function buildTokenSamples(traces: TraceRecord[], calls: LLMCallRecord[]): TokenSample[] {
  const samples: TokenSample[] = [];
  const tracesWithMeasuredCalls = new Set<string>();
  for (const call of calls) {
    const usage = callTokenUsage(call);
    if (!usage.measured) continue;
    if (call.trace_id) tracesWithMeasuredCalls.add(call.trace_id);
    samples.push({ id: call.call_id, model: call.model || t("unknownModel"), conversationId: call.context_id ?? null, createdAt: call.created_at, ...usage });
  }
  for (const trace of traces) {
    if (tracesWithMeasuredCalls.has(trace.trace_id)) continue;
    const modelSpans = trace.spans.filter((span) => span.kind === "model");
    const measured = modelSpans.map((span) => ({ span, usage: spanTokenUsage(span) })).filter((item) => item.usage.measured);
    if (measured.length) {
      measured.forEach(({ span, usage }) => samples.push({ id: span.span_id, model: span.target_id || span.name || t("unknownModel"), conversationId: trace.conversation_id ?? null, createdAt: span.started_at || trace.created_at, ...usage }));
      continue;
    }
    const root = findRootSpan(trace);
    const usage = root ? spanTokenUsage(root) : null;
    if (usage?.measured) samples.push({ id: trace.trace_id, model: t("unknownModel"), conversationId: trace.conversation_id ?? null, createdAt: trace.created_at, ...usage });
  }
  return samples;
}

function groupSamplesByModel(samples: TokenSample[]) {
  const grouped = new Map<string, TokenSample[]>();
  for (const sample of samples) grouped.set(sample.model, [...(grouped.get(sample.model) ?? []), sample]);
  return [...grouped.entries()].map(([model, items]) => ({ model, calls: items.length, input: items.reduce((sum, item) => sum + item.input, 0), output: items.reduce((sum, item) => sum + item.output, 0), total: items.reduce((sum, item) => sum + item.total, 0) })).sort((left, right) => right.total - left.total);
}

function buildSpanRows(spans: TraceSpan[], rootSpanId?: string | null) {
  const byId = new Map(spans.map((span) => [span.span_id, span]));
  const children = new Map<string, TraceSpan[]>();
  for (const span of spans) {
    if (!span.parent_span_id || !byId.has(span.parent_span_id)) continue;
    children.set(span.parent_span_id, [...(children.get(span.parent_span_id) ?? []), span]);
  }
  const sort = (items: TraceSpan[]) => items.sort((left, right) => left.started_at.localeCompare(right.started_at));
  const roots = sort(spans.filter((span) => !span.parent_span_id || !byId.has(span.parent_span_id))).sort((left, right) => left.span_id === rootSpanId ? -1 : right.span_id === rootSpanId ? 1 : left.started_at.localeCompare(right.started_at));
  const rows: Array<{ span: TraceSpan; depth: number }> = [];
  const visited = new Set<string>();
  const visit = (span: TraceSpan, depth: number) => { if (visited.has(span.span_id)) return; visited.add(span.span_id); rows.push({ span, depth }); sort(children.get(span.span_id) ?? []).forEach((child) => visit(child, depth + 1)); };
  roots.forEach((span) => visit(span, 0));
  sort([...spans]).forEach((span) => visit(span, 0));
  return rows;
}

function findRootSpan(trace: TraceRecord) {
  return trace.spans.find((span) => span.span_id === trace.root_span_id) ?? trace.spans.find((span) => span.kind === "agent") ?? trace.spans[0] ?? null;
}

function tracePrompt(trace: TraceRecord) {
  const root = findRootSpan(trace);
  const spanInput = root ? extractUserInput(root) : "";
  if (spanInput) return spanInput;
  const request = trace.events.find((event) => event.kind === "user.request")?.details;
  return readableText(request) || null;
}

function traceFinalResponse(trace: TraceRecord) {
  const root = findRootSpan(trace);
  const spanOutput = root ? extractFinalResponse(root) : "";
  if (spanOutput) return spanOutput;
  const response = [...trace.events].reverse().find((event) => event.kind === "final.response")?.details;
  return readableText(response) || null;
}

function interactionTokenUsage(trace: TraceRecord, calls: LLMCallRecord[]) {
  const callUsages = calls.map(callTokenUsage);
  if (callUsages.some((usage) => usage.measured)) return sumTokenUsage(callUsages);
  return sumTokenUsage(trace.spans.filter((span) => span.kind === "model").map(spanTokenUsage));
}

function traceDuration(trace: TraceRecord) {
  if (!trace.completed_at) return null;
  const started = new Date(trace.created_at).getTime();
  const completed = new Date(trace.completed_at).getTime();
  return Number.isFinite(started) && Number.isFinite(completed) ? Math.max(0, completed - started) : null;
}

function extractUserInput(span: TraceSpan) {
  const input = asRecord(span.input);
  return typeof input?.message === "string" ? input.message : extractContent(span.input);
}

function extractFinalResponse(span: TraceSpan) {
  const output = asRecord(span.output);
  return typeof output?.content === "string" ? output.content : extractContent(span.output);
}

function extractContent(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(extractContent).filter(Boolean).join("\n");
  const record = asRecord(value);
  if (!record) return "";
  if (typeof record.content === "string") return record.content;
  if (record.result != null) return extractContent(record.result) || compactJson(record.result);
  return "";
}

function normalizeRole(value: unknown): LogRole {
  return value === "user" || value === "tool" || value === "system" ? value : "assistant";
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function errorField(error: unknown, key: string) {
  const record = asRecord(error);
  return typeof record?.[key] === "string" ? record[key] as string : "";
}

function stringifyError(error: unknown) {
  if (typeof error === "string") return error;
  return error == null ? "" : compactJson(error);
}

function compactJson(value: unknown) {
  try { return JSON.stringify(value); } catch { return String(value); }
}

function periodRange(period: Period) {
  const end = endOfDay(new Date());
  const start = addDays(startOfDay(new Date()), period === "day" ? 0 : period === "week" ? -6 : -29);
  return { start, end };
}

function inRange(iso: string, start: Date, end: Date) {
  const value = new Date(iso).getTime();
  return Number.isFinite(value) && value >= start.getTime() && value <= end.getTime();
}

function startOfDay(date: Date) { const value = new Date(date); value.setHours(0, 0, 0, 0); return value; }
function endOfDay(date: Date) { const value = new Date(date); value.setHours(23, 59, 59, 999); return value; }
function addDays(date: Date, days: number) { const value = new Date(date); value.setDate(value.getDate() + days); return value; }
function toDateKey(date: Date) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`; }
function formatDate(date: Date) { return date.toLocaleDateString(currentLocale()); }
function formatDateTime(iso: string) { return new Date(iso).toLocaleString(currentLocale()); }
function formatMonth(date: Date) { return `${date.getFullYear()}/${String(date.getMonth() + 1).padStart(2, "0")}`; }
function formatNumber(value: number | null) { return value == null ? "—" : Math.round(value).toLocaleString(currentLocale()); }
function formatEstimatedNumber(value: number | null, estimated: boolean) { return value == null ? "—" : `${estimated ? "≈" : ""}${formatNumber(value)}`; }
function formatDuration(value: number | null) { return value == null ? "—" : value < 1000 ? `${value.toFixed(0)} ms` : `${(value / 1000).toFixed(2)} s`; }
function formatRate(value: number | null) { return value == null ? "—" : `${value.toFixed(1)} Token/s`; }
function formatEstimatedRate(value: number | null, estimated: boolean) { return value == null ? "—" : `${estimated ? "≈" : ""}${formatRate(value)}`; }
function periodLabel(period: Period) { return period === "day" ? t("period.day") : period === "week" ? t("period.week") : t("period.month"); }
function heatColor(value: number, max: number) { if (!value || !max) return "bg-slate-100"; const ratio = value / max; return ratio <= 0.25 ? "bg-emerald-100" : ratio <= 0.5 ? "bg-emerald-300" : ratio <= 0.75 ? "bg-emerald-500" : "bg-emerald-700"; }
