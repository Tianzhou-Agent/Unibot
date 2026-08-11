import { api } from "@/lib/api";

// Chat completion waits for WAL durability; MySQL visibility can trail it briefly.
const SESSION_VISIBILITY_RETRY_DELAYS_MS = [50, 150, 300, 500] as const;

/** 服务端聚合的个人总览（design 12.2），前端不再拉全量 LLM Call 自行聚合。 */
export interface ObsOverview {
  range: string;
  start?: string;
  end?: string;
  trace_count: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  total_tokens: number;
  error_count: number;
  active_days: number;
  conversation_count: number;
  per_model: {
    model: string;
    call_count: number;
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
  }[];
  daily: {
    day: string | null;
    trace_count: number;
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
  }[];
}

/** 对话详情 DTO：调用树、错误诊断和原始日志定位所需的结构化数据。 */
export interface ObsSpan {
  span_id: string;
  otel_span_id: string;
  trace_id: string;
  parent_span_id: string | null;
  sequence_no: number;
  kind: string;
  name: string;
  target_id: string | null;
  model: string | null;
  status: string;
  started_at: string | null;
  first_output_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  ttft_ms: number | null;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  input: unknown;
  output: unknown;
  attributes: Record<string, unknown>;
  error: unknown;
  raw_io_path: string | null;
  raw_io_status: string | null;
}

export interface ObsEvent {
  event_id: string;
  trace_id: string;
  span_id: string | null;
  name: string;
  status: string | null;
  occurred_at: string | null;
  attributes: Record<string, unknown>;
}

export interface ObsTrace {
  trace_id: string;
  legacy_trace_id: string | null;
  root_span_id: string | null;
  session_id: string | null;
  user_id: string;
  tenant_id: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  message_count: number;
  compression_count: number;
  error_count: number;
  attributes: Record<string, unknown>;
}

export interface ObsSessionDetail {
  session_id: string;
  traces: ObsTrace[];
  spans: ObsSpan[];
  events: ObsEvent[];
}

export interface ObsRawLog {
  status: string;
  detail: unknown;
}

export async function getObsOverview(range: string): Promise<ObsOverview> {
  return api.get<ObsOverview>(`/obs/overview?range=${encodeURIComponent(range)}`);
}

async function getObsSessionEventually(path: string): Promise<ObsSessionDetail | null> {
  let detail = await api.get<ObsSessionDetail | null>(path);
  for (const delayMs of SESSION_VISIBILITY_RETRY_DELAYS_MS) {
    if (detail) return detail;
    await new Promise((resolve) => window.setTimeout(resolve, delayMs));
    detail = await api.get<ObsSessionDetail | null>(path);
  }
  return detail;
}

export async function getObsSession(sessionId: string): Promise<ObsSessionDetail | null> {
  return getObsSessionEventually(`/obs/sessions/${encodeURIComponent(sessionId)}`);
}

export async function getAdminObsSession(sessionId: string): Promise<ObsSessionDetail | null> {
  return getObsSessionEventually(`/admin/obs/sessions/${encodeURIComponent(sessionId)}`);
}

export async function getRawLogs(traceId: string, spanId: string): Promise<ObsRawLog | null> {
  return api.get<ObsRawLog | null>(
    `/obs/raw-logs?trace_id=${encodeURIComponent(traceId)}&span_id=${encodeURIComponent(spanId)}`,
  );
}
