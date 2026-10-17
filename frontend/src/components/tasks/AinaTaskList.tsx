import { useTranslation } from "react-i18next";
import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Hourglass, LoaderCircle, XCircle } from "lucide-react";
import { api } from "@/lib/api";
import { classNames } from "@/lib/utils";
import type { AinaTask } from "@/types";

const POLL_MS = 3000;

/** Background tasks of long-running AINA tools in this conversation; polled while any of them runs. */
export function AinaTaskList({
  conversationId,
  refreshKey,
  onSettled,
}: {
  conversationId: string | null;
  refreshKey: number;
  onSettled: () => void;
}) {
  const { t } = useTranslation("tasks");
  const [tasks, setTasks] = useState<AinaTask[]>([]);
  const running = useRef<Set<string>>(new Set());
  const onSettledRef = useRef(onSettled);
  onSettledRef.current = onSettled;

  const load = useCallback(async () => {
    if (!conversationId) {
      setTasks([]);
      return;
    }
    let items: AinaTask[];
    try {
      items = await api.get<AinaTask[]>(`/conversations/${encodeURIComponent(conversationId)}/aina-tasks`);
    } catch {
      return;
    }
    // A task that just finished posted its result to the conversation.
    const settled = items.some((task) => task.status !== "running" && running.current.has(task.id));
    running.current = new Set(items.filter((task) => task.status === "running").map((task) => task.id));
    setTasks(items);
    if (settled) onSettledRef.current();
  }, [conversationId]);

  useEffect(() => {
    running.current = new Set();
  }, [conversationId]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const anyRunning = tasks.some((task) => task.status === "running");
  useEffect(() => {
    if (!anyRunning) return;
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [anyRunning, load]);

  if (!tasks.length) return null;

  return (
    <section className="overflow-hidden rounded-xl border border-line-strong bg-app-soft shadow-soft" aria-label={t("background.aria")}>
      <div className="flex items-center gap-2.5 px-3.5 pt-2.5">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center text-ink-muted">
          <Hourglass className="h-4 w-4" />
        </span>
        <strong className="text-[13px] text-ink">{t("background.title")}</strong>
      </div>
      <div className="max-h-40 space-y-1 overflow-y-auto px-3 pb-2 pt-1">
        {tasks.map((task) => <AinaTaskRow key={task.id} task={task} />)}
      </div>
    </section>
  );
}

function AinaTaskRow({ task }: { task: AinaTask }) {
  const { t } = useTranslation("tasks");
  const Icon = task.status === "completed" ? CheckCircle2 : task.status === "failed" ? XCircle : LoaderCircle;
  const tone = task.status === "completed" ? "text-success" : task.status === "failed" ? "text-danger" : "text-accent";
  const percent = task.status === "running" && task.progress !== null && task.total
    ? Math.min(100, Math.round((task.progress / task.total) * 100))
    : null;
  const detail = task.status === "failed" ? task.error : task.status === "running" ? task.message : null;
  return (
    <div className={classNames("flex min-h-9 items-start gap-2 rounded-md px-2 py-2", task.status === "running" ? "bg-accent-soft" : "")}>
      <Icon className={classNames("mt-0.5 h-3.5 w-3.5 shrink-0", tone, task.status === "running" ? "animate-spin" : "")} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <span className={classNames("text-[12px] font-semibold", task.status === "running" ? "text-ink" : "text-ink-muted")}>{task.title}</span>
          <span className={classNames("text-[9.5px] font-bold", tone)}>
            {t(`background.status.${task.status}`)}{percent !== null ? ` · ${percent}%` : ""}
          </span>
        </div>
        {detail ? <p className="mt-0.5 line-clamp-2 text-[10.5px] text-ink-muted">{detail}</p> : null}
      </div>
    </div>
  );
}
