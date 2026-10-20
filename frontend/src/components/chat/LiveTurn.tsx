import { MarkdownContent } from "@/components/chat/MarkdownContent";
import { ReasoningBlock } from "@/components/chat/ReasoningBlock";
import { ToolCallCard } from "@/components/chat/ToolCallCard";
import type { StreamEvent } from "@/lib/api";

// The in-flight turn, in arrival order, until the persisted conversation replaces it.
export type LiveItem =
  | { kind: "text"; key: string; text: string }
  | { kind: "reasoning"; key: string; text: string }
  | {
      kind: "tool";
      key: string;
      callId: string;
      name: string;
      argumentsText: string;
      resultText?: string;
      state: "running" | "success" | "error";
    };

export function applyLiveEvent(items: LiveItem[], event: StreamEvent): LiveItem[] {
  if (event.type === "reasoning.delta") {
    const last = items[items.length - 1];
    if (last?.kind === "reasoning") return [...items.slice(0, -1), { ...last, text: last.text + event.delta }];
    return [...items, { kind: "reasoning", key: `reasoning-${items.length}`, text: event.delta }];
  }
  if (event.type === "message.delta") {
    const last = items[items.length - 1];
    if (last?.kind === "text") return [...items.slice(0, -1), { ...last, text: last.text + event.delta }];
    return [...items, { kind: "text", key: `text-${items.length}`, text: event.delta }];
  }
  if (event.type === "tool.requested") {
    return [...items, {
      kind: "tool",
      key: `tool-${items.length}`,
      callId: event.call_id,
      name: event.name,
      argumentsText: event.arguments,
      state: "running",
    }];
  }
  if (event.type === "tool.completed") {
    for (let index = items.length - 1; index >= 0; index -= 1) {
      const item = items[index];
      if (item.kind !== "tool" || item.callId !== event.call_id || item.state !== "running") continue;
      const next = [...items];
      next[index] = { ...item, state: event.status === "failed" ? "error" : "success", resultText: event.result ?? undefined };
      return next;
    }
    return items;
  }
  return items;
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
          state={item.state}
          compact={compact}
          debugMode={debugMode}
        />
      ))}
    </>
  );
}
