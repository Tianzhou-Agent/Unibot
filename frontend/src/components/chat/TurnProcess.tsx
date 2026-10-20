import { useTranslation } from "react-i18next";
import { ChevronDown, Lightbulb, MessageSquareText, Sparkles, Wrench } from "lucide-react";
import { classNames } from "@/lib/utils";
import type { BackendMessage } from "@/types";

export type TurnProcess = {
  /** Everything between the user message and the final answer. */
  stepIds: ReadonlySet<string>;
  /** The final answer, whose own thinking is a step too: it folds with the rest. */
  answerId: string;
  durationMs: number | null;
};

/** A message posted between turns, e.g. a background AINA task's result (backend ``result_message_id``). */
function isOutOfTurn(message: BackendMessage): boolean {
  return message.id.startsWith("msg_aina_task_");
}

/** Saved progress messages are not final answers, even though their individual model call has ended. */
export function completedAnswerIds(messages: BackendMessage[], lastTurnFinished: boolean): ReadonlySet<string> {
  const visible = messages.filter((message) => message.role !== "system");
  return new Set(visible.filter((message, index) => {
    if (message.role !== "assistant" || !message.content || message.tool_calls?.length) return false;
    if (isOutOfTurn(message)) return true;
    const next = visible[index + 1];
    return next ? next.role === "user" || isOutOfTurn(next) : lastTurnFinished;
  }).map((message) => message.id));
}

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
    const following = messages.slice(start + 1, last ? messages.length : starts[turnIndex + 1]);
    // Out-of-turn messages end the turn; they are shown on their own, never folded into its steps.
    const end = following.findIndex(isOutOfTurn);
    const turn = (end < 0 ? following : following.slice(0, end)).filter((message) => message.role !== "system");
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

export function TurnProcessToggle({ durationMs, open, onToggle, active = false, activityType = "reasoning", preview }: {
  durationMs: number | null;
  open: boolean;
  onToggle: () => void;
  active?: boolean;
  activityType?: "reasoning" | "text" | "tool" | "activity";
  preview?: string | null;
}) {
  const { t } = useTranslation("chat");
  const Icon = { reasoning: Lightbulb, text: MessageSquareText, tool: Wrench, activity: Sparkles }[activityType];
  const detail = open ? undefined : preview?.replace(/\s+/g, " ").trim();
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      className="flex w-full items-center gap-1 border-b border-line pb-2 text-left text-[13px] text-ink-subtle transition-colors hover:text-ink-muted"
    >
      {active ? (
        <>
          <Icon className="thinking-glow mr-1 h-4 w-4 shrink-0 text-accent" aria-hidden />
          <span className="shrink-0">{t(`process.type.${activityType}`)}</span>
          {detail ? <span className="mx-1 shrink-0" aria-hidden>·</span> : null}
        </>
      ) : null}
      {!active || detail ? <span className={classNames("min-w-0 truncate tabular-nums", active && "thinking-shimmer [direction:rtl]")}>
        <span className="[direction:ltr] [unicode-bidi:isolate]">
          {active ? detail : durationMs === null ? t("process.completed") : t("process.completedIn", { duration: formatDuration(durationMs, t) })}
        </span>
      </span> : null}
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
