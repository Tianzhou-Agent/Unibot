import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  CircleDashed,
  ListTodo,
  LoaderCircle,
  SkipForward,
  XCircle,
} from "lucide-react";
import { api, apiUrl } from "@/lib/api";
import { classNames } from "@/lib/utils";
import type { SessionTaskNode, TaskStatus, TaskTreeSnapshot } from "@/types";

export function TaskTreeWidget({ sessionId }: { sessionId: string | null }) {
  const [snapshot, setSnapshot] = useState<TaskTreeSnapshot | null>(null);
  const [open, setOpen] = useState(true);

  const load = useCallback(async () => {
    if (!sessionId) {
      setSnapshot(null);
      return;
    }
    try {
      setSnapshot(await api.get<TaskTreeSnapshot>(`/tasks?session_id=${encodeURIComponent(sessionId)}`));
    } catch {
      setSnapshot(null);
    }
  }, [sessionId]);

  useEffect(() => {
    void load();
    if (!sessionId) return;
    const source = new EventSource(
      apiUrl(`/tasks/events?session_id=${encodeURIComponent(sessionId)}`),
      { withCredentials: true },
    );
    const changed = () => void load();
    source.addEventListener("task.changed", changed);
    return () => source.close();
  }, [load, sessionId]);

  const progress = useMemo(() => taskProgress(snapshot?.tasks ?? []), [snapshot?.tasks]);
  if (!snapshot?.tasks.length) return null;

  return (
    <section className="overflow-hidden rounded-lg border border-line bg-white shadow-soft" aria-label="任务进度">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-app-soft"
        aria-expanded={open}
      >
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent">
          <ListTodo className="h-4 w-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <strong className="text-[13px] text-ink">任务进度</strong>
            <span className="rounded-full bg-app-soft px-2 py-0.5 text-[10px] font-bold text-ink-muted">
              {progress.done}/{progress.total}
            </span>
          </span>
          <span className="mt-1 block h-1.5 overflow-hidden rounded-full bg-line">
            <span
              className="block h-full rounded-full bg-accent transition-[width]"
              style={{ width: `${progress.total ? (progress.done / progress.total) * 100 : 0}%` }}
            />
          </span>
        </span>
        <span className="font-mono text-[9.5px] text-ink-subtle">r{snapshot.revision}</span>
        {open ? <ChevronDown className="h-4 w-4 text-ink-muted" /> : <ChevronRight className="h-4 w-4 text-ink-muted" />}
      </button>
      {open ? (
        <div className="border-t border-line px-3 py-2.5">
          <div className="space-y-1">
            {snapshot.tasks.map((task) => <TaskRow key={task.task_id} task={task} />)}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function TaskRow({ task }: { task: SessionTaskNode }) {
  const Icon = statusIcon(task.status);
  return (
    <div>
      <div
        className={classNames(
          "flex min-h-9 items-start gap-2 rounded-md px-2 py-2",
          task.status === "in_progress" || task.status === "verifying" ? "bg-accent-soft" : "",
        )}
        style={{ marginLeft: task.depth * 18 }}
      >
        <Icon className={classNames("mt-0.5 h-3.5 w-3.5 shrink-0", statusTone(task.status))} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className={classNames("text-[12px] font-semibold", task.status === "completed" || task.status === "skipped" ? "text-ink-muted" : "text-ink")}>{task.title}</span>
            <span className={classNames("text-[9.5px] font-bold", statusTone(task.status))}>{statusLabel(task.status)}</span>
          </div>
          {task.verification_reason ? (
            <p className="mt-0.5 line-clamp-2 text-[10.5px] leading-relaxed text-ink-muted">{task.verification_reason}</p>
          ) : task.description ? (
            <p className="mt-0.5 line-clamp-1 text-[10.5px] text-ink-muted">{task.description}</p>
          ) : null}
        </div>
      </div>
      {task.children.map((child) => <TaskRow key={child.task_id} task={child} />)}
    </div>
  );
}

function taskProgress(tasks: SessionTaskNode[]): { done: number; total: number } {
  const leaves: SessionTaskNode[] = [];
  const visit = (task: SessionTaskNode) => {
    if (!task.children.length) leaves.push(task);
    else task.children.forEach(visit);
  };
  tasks.forEach(visit);
  return {
    total: leaves.length,
    done: leaves.filter((task) => task.status === "completed" || task.status === "skipped").length,
  };
}

function statusIcon(status: TaskStatus) {
  if (status === "completed") return CheckCircle2;
  if (status === "in_progress") return LoaderCircle;
  if (status === "verifying") return CircleDashed;
  if (status === "skipped") return SkipForward;
  if (status === "failed") return XCircle;
  return Circle;
}

function statusLabel(status: TaskStatus): string {
  return {
    pending: "待处理",
    in_progress: "进行中",
    verifying: "验证中",
    completed: "已完成",
    skipped: "已跳过",
    failed: "失败",
  }[status];
}

function statusTone(status: TaskStatus): string {
  return {
    pending: "text-ink-subtle",
    in_progress: "text-accent",
    verifying: "text-warning",
    completed: "text-success",
    skipped: "text-ink-muted",
    failed: "text-danger",
  }[status];
}
