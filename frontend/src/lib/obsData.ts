import { api } from "@/lib/api";
import type { LLMCallRecord } from "@/types";

const LLM_CALL_PAGE_SIZE = 500;

export async function loadAllPersonalLlmCalls(actorQuery = ""): Promise<LLMCallRecord[]> {
  const calls: LLMCallRecord[] = [];
  let offset = 0;
  while (true) {
    const page = await api.get<LLMCallRecord[]>(
      `/llm-calls?${actorQuery ? `${actorQuery}&` : ""}limit=${LLM_CALL_PAGE_SIZE}&offset=${offset}`,
    );
    calls.push(...page);
    if (page.length < LLM_CALL_PAGE_SIZE) return calls;
    offset += page.length;
  }
}
