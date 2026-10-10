import { useTranslation } from "react-i18next";
import { ChevronDown } from "lucide-react";
import { classNames } from "@/lib/utils";
import type { BackendMessage } from "@/types";

export type TurnProcess = {
  /** Everything between the user message and the final answer. */
  stepIds: ReadonlySet<string>;
  /** The final answer, whose own thinking is a step too: it folds with the rest. */
  answerId: string;
  durationMs: number | null;
};

/**
 * The intermediate steps of every finished turn, keyed by the turn's user message id.
 * A turn is finished once it ends in an assistant answer; the last turn is left out while it can still change.
 */
export function finishedTurnProcesses(messages: BackendMessage[], lastTurnFinished: boolean): Map<string, TurnProcess> {
  const processes = new Map<string, TurnProcess>();
  const starts = messages.flatMap((message, index) => message.role === "user" ? [index] : []);
  starts.forEach((start, turnIndex) => {
    const last = turnIndex === starts.length - 1;
    if (last && !lastTurnFinished) return;
    const turn = messages.slice(start + 1, last ? messages.length : starts[turnIndex + 1]).filter((message) => message.role !== "system");
    const answer = turn[turn.length - 1];
    if (!answer || answer.role !== "assistant" || !answer.content || answer.tool_calls?.length) return;
    if (turn.length < 2 && !answer.reasoning) return;
    // The user message is saved when the run starts and the rest when it ends, so this spans the whole run.
    const elapsed = Date.parse(answer.created_at) - Date.parse(messages[start].created_at);
    processes.set(messages[start].id, {
      stepIds: new Set(turn.slice(0, -1).map((message) => message.id)),
      answerId: answer.id,
      durationMs: Number.isFinite(elapsed) ? Math.max(1000, elapsed) : null,
    });
  });
  return processes;
}

export function TurnProcessToggle({ durationMs, open, onToggle }: { durationMs: number | null; open: boolean; onToggle: () => void }) {
  const { t } = useTranslation("chat");
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      className="flex w-full items-center gap-1 border-b border-line pb-2 text-left text-[13px] text-ink-subtle transition-colors hover:text-ink-muted"
    >
      <span className="truncate tabular-nums">
        {durationMs === null ? t("process.completed") : t("process.completedIn", { duration: formatDuration(durationMs, t) })}
      </span>
      <ChevronDown className={classNames("h-3.5 w-3.5 shrink-0 transition-transform", open && "rotate-180")} />
    </button>
  );
}

function formatDuration(ms: number, t: (key: string, options: Record<string, number>) => string): string {
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor(total / 60) % 60;
  const s = total % 60;
  if (h) return t("process.hours", { h, m, s });
  if (m) return t("process.minutes", { m, s });
  return t("process.seconds", { s });
}
