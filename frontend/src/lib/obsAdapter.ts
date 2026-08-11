/**
 * 把后端 OBS 聚合 DTO（/obs/sessions/{id}）适配为现有组件消费的
 * TraceRecord / LLMCallRecord 形状，保持页面视觉结构不变。
 * OTel 定位信息（otel_trace_id/otel_span_id）注入 span.attributes，
 * 供原始日志面板按需加载完整 IO（/obs/raw-logs）。
 */
import type { ObsSessionDetail, ObsSpan } from "@/lib/obsApi";
import type { LLMCallRecord, TraceEvent, TraceRecord, TraceSpan } from "@/types";

export interface AdaptedSession {
  traces: TraceRecord[];
  calls: LLMCallRecord[];
}

function adaptModelResponse(span: ObsSpan): Record<string, unknown> | undefined {
  const output = span.output && typeof span.output === "object" && !Array.isArray(span.output)
    ? span.output as Record<string, unknown>
    : undefined;
  const hasTokenUsage = span.input_tokens > 0 || span.output_tokens > 0 || span.cache_read_tokens > 0;
  const usageEstimated = span.attributes.usage_estimated === true;
  if (!hasTokenUsage || (output?.usage && typeof output.usage === "object")) return output;
  return {
    ...(output ?? {}),
    usage: {
      prompt_tokens: span.input_tokens,
      completion_tokens: span.output_tokens,
      total_tokens: span.input_tokens + span.output_tokens,
      ...(usageEstimated ? { estimated: true, source: "estimated" } : {}),
      prompt_tokens_details: { cached_tokens: span.cache_read_tokens },
    },
  };
}

function adaptSpan(span: ObsSpan): TraceSpan {
  const hasTokenUsage = span.input_tokens > 0 || span.output_tokens > 0 || span.cache_read_tokens > 0;
  return {
    span_id: span.span_id,
    parent_span_id: span.parent_span_id,
    kind: (span.kind as TraceSpan["kind"]) ?? "internal",
    name: span.name,
    status: (span.status as TraceSpan["status"]) ?? "completed",
    target_id: span.target_id ?? undefined,
    target_version: typeof span.attributes.target_version === "string"
      ? span.attributes.target_version
      : undefined,
    started_at: span.started_at ?? new Date().toISOString(),
    first_output_at: span.first_output_at ?? undefined,
    completed_at: span.completed_at ?? undefined,
    duration_ms: span.duration_ms ?? undefined,
    attempt_no: 1,
    input: span.input ?? undefined,
    output: span.output ?? undefined,
    attributes: {
      ...(span.attributes ?? {}),
      otel_trace_id: span.trace_id,
      otel_span_id: span.otel_span_id,
      raw_io_path: span.raw_io_path ?? undefined,
      raw_io_status: span.raw_io_status ?? undefined,
      ...(hasTokenUsage ? {
        input_tokens: span.input_tokens,
        output_tokens: span.output_tokens,
        cache_read_tokens: span.cache_read_tokens,
      } : {}),
      ttft_ms: span.ttft_ms ?? span.attributes.ttft_ms,
    },
    error: (span.error as Record<string, unknown> | undefined) ?? undefined,
  };
}

function adaptEvent(event: ObsSessionDetail["events"][number]): TraceEvent {
  return {
    timestamp: event.occurred_at ?? new Date().toISOString(),
    kind: event.name,
    status: event.status ?? "completed",
    details: (event.attributes ?? {}) as Record<string, unknown>,
  };
}

function spanErrorMessage(error: unknown): string | undefined {
  if (typeof error === "string" && error) return error;
  if (error && typeof error === "object") {
    const message = (error as Record<string, unknown>).message;
    if (typeof message === "string" && message) return message;
  }
  return undefined;
}

export function adaptSessionDetail(session: ObsSessionDetail): AdaptedSession {
  const spansByTrace = new Map<string, TraceSpan[]>();
  for (const span of session.spans) {
    const list = spansByTrace.get(span.trace_id) ?? [];
    list.push(adaptSpan(span));
    spansByTrace.set(span.trace_id, list);
  }
  const calls: LLMCallRecord[] = [];
  for (const span of session.spans) {
    if (span.kind !== "model") continue;
    calls.push({
      call_id: span.otel_span_id,
      trace_id: span.trace_id,
      span_id: span.span_id,
      context_type: "conversation",
      context_id: session.session_id,
      endpoint: "",
      model: span.model ?? span.name,
      status: (span.status as LLMCallRecord["status"]) ?? "completed",
      request: (span.input as Record<string, unknown> | undefined) ?? {},
      response: adaptModelResponse(span),
      duration_ms: span.duration_ms ?? undefined,
      ttft_ms: span.ttft_ms ?? undefined,
      error: spanErrorMessage(span.error),
      created_at: span.started_at ?? new Date().toISOString(),
      completed_at: span.completed_at ?? undefined,
    });
  }
  const traces: TraceRecord[] = [...session.traces]
    .sort((left, right) => {
      const started = (left.started_at ?? "").localeCompare(right.started_at ?? "");
      if (started !== 0) return started;
      return left.trace_id.localeCompare(right.trace_id);
    })
    .map((trace) => ({
    trace_id: trace.trace_id,
    root_span_id: trace.root_span_id,
    conversation_id: trace.session_id,
    user_id: trace.user_id,
    tenant_id: trace.tenant_id,
    status: (trace.status as TraceRecord["status"]) ?? "completed",
    events: session.events.filter((event) => event.trace_id === trace.trace_id).map(adaptEvent),
    spans: spansByTrace.get(trace.trace_id) ?? [],
    created_at: trace.started_at ?? new Date().toISOString(),
    completed_at: trace.completed_at ?? undefined,
    }));
  return { traces, calls };
}
