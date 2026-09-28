import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";
import { MarkdownContent } from "@/components/chat/MarkdownContent";
import { ToolCallCard } from "@/components/chat/ToolCallCard";
import type { StreamEvent } from "@/lib/api";
import { classNames } from "@/lib/utils";

// The in-flight turn, in arrival order, until the persisted conversation replaces it.
export type LiveItem =
  | { kind: "text"; key: string; text: string }
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
      {items.map((item) => item.kind === "text" ? (
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

const THINKING_WORDS = [
  "思考中",
  "琢磨中",
  "推敲中",
  "酝酿中",
  "斟酌中",
  "盘算中",
  "构思中",
  "梳理思路",
  "串联线索",
  "组织语言",
  "捋一捋",
  "灵感加载中",
];

export function ThinkingIndicator({ label, compact = false }: { label?: string | null; compact?: boolean }) {
  const [index, setIndex] = useState(() => Math.floor(Math.random() * THINKING_WORDS.length));
  useEffect(() => {
    if (label) return;
    const timer = window.setInterval(() => {
      // Skip ahead by a random amount so the same word never repeats back to back.
      setIndex((current) => (current + 1 + Math.floor(Math.random() * (THINKING_WORDS.length - 1))) % THINKING_WORDS.length);
    }, 2400);
    return () => window.clearInterval(timer);
  }, [label]);
  const text = label || `${THINKING_WORDS[index]}…`;

  return (
    <div className={classNames("flex items-center gap-2", compact ? "min-h-7" : "min-h-8")} role="status">
      <Sparkles className={classNames("thinking-glow shrink-0 text-accent", compact ? "h-3.5 w-3.5" : "h-4 w-4")} aria-hidden />
      <span key={text} className={classNames("thinking-shimmer font-medium", compact ? "text-[12px]" : "text-[13px]")} aria-hidden>
        {text}
      </span>
      <span className="sr-only">{label || "正在思考"}</span>
    </div>
  );
}
