/**
 * OBS 数据加载（设计迁移后）：
 * 个人总览、对话详情与原始日志全部走后端聚合 DTO，
 * 前端不再循环分页加载用户的全部 LLM Call 后自行聚合。
 * 具体接口封装见 ./obsApi。
 */
import { api } from "@/lib/api";
import type { LLMCallRecord, TraceRecord } from "@/types";

const LEGACY_LLM_CALL_PAGE_SIZE = 500;

export interface LegacyObsData {
  traces: TraceRecord[];
  calls: LLMCallRecord[];
}

async function loadAllLegacyLlmCalls(path: string): Promise<LLMCallRecord[]> {
  const calls: LLMCallRecord[] = [];
  let offset = 0;
  while (true) {
    const separator = path.includes("?") ? "&" : "?";
    const page = await api.get<LLMCallRecord[]>(
      `${path}${separator}limit=${LEGACY_LLM_CALL_PAGE_SIZE}&offset=${offset}`,
    );
    calls.push(...page);
    if (page.length < LEGACY_LLM_CALL_PAGE_SIZE) return calls;
    offset += page.length;
  }
}

function sessionData(sessionId: string, traces: TraceRecord[], calls: LLMCallRecord[]): LegacyObsData {
  const sessionTraces = traces.filter((trace) => trace.conversation_id === sessionId);
  const traceIds = new Set(sessionTraces.map((trace) => trace.trace_id));
  return {
    traces: sessionTraces,
    calls: calls.filter((call) => (
      (call.trace_id != null && traceIds.has(call.trace_id)) || call.context_id === sessionId
    )),
  };
}

/** Migration-only fallback. The normal OBS path never loads the legacy full history. */
export async function loadLegacyPersonalObsData(actorQuery = ""): Promise<LegacyObsData> {
  const querySuffix = actorQuery ? `?${actorQuery}` : "";
  const callPath = `/llm-calls${querySuffix}`;
  const [traces, calls] = await Promise.all([
    api.get<TraceRecord[]>(`/traces${querySuffix}`),
    loadAllLegacyLlmCalls(callPath),
  ]);
  return { traces, calls };
}

export async function loadLegacyPersonalObsSession(
  sessionId: string,
  actorQuery = "",
): Promise<LegacyObsData> {
  const data = await loadLegacyPersonalObsData(actorQuery);
  return sessionData(sessionId, data.traces, data.calls);
}

export async function loadLegacyAdminObsSession(sessionId: string): Promise<LegacyObsData> {
  const [traces, calls] = await Promise.all([
    api.get<TraceRecord[]>("/admin/traces"),
    loadAllLegacyLlmCalls("/admin/llm-calls"),
  ]);
  return sessionData(sessionId, traces, calls);
}

export { getAdminObsSession, getObsOverview, getObsSession, getRawLogs } from "@/lib/obsApi";
export type {
  ObsEvent,
  ObsOverview,
  ObsRawLog,
  ObsSessionDetail,
  ObsSpan,
  ObsTrace,
} from "@/lib/obsApi";
