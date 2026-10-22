import { MarkdownContent } from "@/components/chat/MarkdownContent";
import { ReasoningBlock } from "@/components/chat/ReasoningBlock";
import { ToolCallCard } from "@/components/chat/ToolCallCard";
import { StepDuration } from "@/components/chat/TurnProcess";
import type { StreamEvent } from "@/lib/api";

// The in-flight turn, in arrival order, until the persisted conversation replaces it. Times are epoch ms.
export type LiveItem =
  | { kind: "text"; key: string; text: string; startedAt: number }
  | { kind: "reasoning"; key: string; text: string; startedAt: number }
  | {
      kind: "tool";
      key: string;
      callId: string;
      name: string;
      argumentsText: string;
      resultText?: string;
      /** preparing: the model is still writing the call's arguments. */
      state: "preparing" | "running" | "success" | "error";
      startedAt: number;
      /** When the call began to run (after its arguments were written). */
      runningAt?: number;
      endedAt?: number;
    };

export function applyLiveEvent(items: LiveItem[], event: StreamEvent, now = Date.now()): LiveItem[] {
  if (event.type === "reasoning.delta") {
    const last = items[items.length - 1];
    if (last?.kind === "reasoning") return [...items.slice(0, -1), { ...last, text: last.text + event.delta }];
    return [...items, { kind: "reasoning", key: `reasoning-${items.length}`, text: event.delta, startedAt: now }];
  }
  if (event.type === "message.delta") {
    const last = items[items.length - 1];
    if (last?.kind === "text") return [...items.slice(0, -1), { ...last, text: last.text + event.delta }];
    return [...items, { kind: "text", key: `text-${items.length}`, text: event.delta, startedAt: now }];
  }
  if (event.type === "tool.preparing") {
    return [...items, { kind: "tool", key: `tool-${items.length}`, callId: "", name: event.name, argumentsText: "", state: "preparing", startedAt: now }];
  }
  if (event.type === "tool.requested") {
    // The call the model was writing under this name, if its start was announced.
    const index = items.findIndex((item) => item.kind === "tool" && item.state === "preparing" && item.name === event.name);
    const prepared = index >= 0 ? items[index] : null;
    const running: LiveItem = {
      kind: "tool",
      key: prepared?.key ?? `tool-${items.length}`,
      callId: event.call_id,
      name: event.name,
      argumentsText: event.arguments,
      state: "running",
      startedAt: prepared?.startedAt ?? now,
      runningAt: now,
    };
    return prepared ? items.map((item, position) => (position === index ? running : item)) : [...items, running];
  }
  if (event.type === "tool.completed") {
    for (let index = items.length - 1; index >= 0; index -= 1) {
      const item = items[index];
      if (item.kind !== "tool" || item.callId !== event.call_id || item.state !== "running") continue;
      const next = [...items];
      next[index] = { ...item, state: event.status === "failed" ? "error" : "success", resultText: event.result ?? undefined, endedAt: now };
      return next;
    }
    return items;
  }
  return items;
}

/** What the live turn is doing now and since when; after a call ends the model is thinking about its result. */
export function currentStep(items: LiveItem[], turnStartedAt: number): {
  kind: "thinking" | "responding" | "preparing" | "running";
  tool?: string;
  since: number;
} {
  const last = items[items.length - 1];
  if (!last) return { kind: "thinking", since: turnStartedAt };
  if (last.kind === "text") return { kind: "responding", since: last.startedAt };
  if (last.kind === "reasoning") return { kind: "thinking", since: last.startedAt };
  if (last.state === "preparing") return { kind: "preparing", tool: last.name, since: last.startedAt };
  if (last.state === "running") return { kind: "running", tool: last.name, since: last.runningAt ?? last.startedAt };
  return { kind: "thinking", since: last.endedAt ?? last.startedAt };
}

export function LiveTurn({ items, compact = false, debugMode = false }: {
  items: LiveItem[];
  compact?: boolean;
  debugMode?: boolean;
}) {
  return (
    <>
      {items.map((item, index) => item.kind === "reasoning" ? (
        <ReasoningBlock key={item.key} text={item.text} active={index === items.length - 1} compact={compact} />
      ) : item.kind === "text" ? (
        // No copy/feedback actions yet: the persisted message that replaces this carries them.
        <div key={item.key} className="py-0.5">
          <MarkdownContent content={item.text} />
        </div>
      ) : (
        <ToolCallCard
          key={item.key}
          name={item.name}
          argumentsText={item.argumentsText}
          resultText={item.resultText}
          state={item.state === "preparing" ? "queued" : item.state}
          compact={compact}
          debugMode={debugMode}
          duration={<StepDuration startedAt={item.startedAt} endedAt={item.endedAt} />}
        />
      ))}
    </>
  );
}
