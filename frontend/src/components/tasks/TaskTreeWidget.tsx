import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
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
  const [open, setOpen] = useState(false);

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
    <section className="overflow-hidden rounded-xl border border-line-strong bg-app-soft shadow-soft" aria-label="任务进度">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left transition hover:bg-line/50"
        aria-expanded={open}
      >
        <span className="flex h-6 w-6 shrink-0 items-center justify-center text-ink-muted">
          <ListTodo className="h-4 w-4" />
        </span>
        <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-1.5 gap-y-0.5">
          <strong className="mr-1 text-[13px] text-ink">任务</strong>
          <TaskCount value={progress.completed} label="已完成" />
          <span className="text-ink-subtle">·</span>
          <TaskCount value={progress.inProgress} label="进行中" />
          <span className="text-ink-subtle">·</span>
          <TaskCount value={progress.pending} label="待处理" />
          {progress.failed ? (
            <>
              <span className="text-ink-subtle">·</span>
              <TaskCount value={progress.failed} label="失败" danger />
            </>
          ) : null}
          <span className="sr-only">修订版本 {snapshot.revision}</span>
        </span>
        {open ? <ChevronDown className="h-4 w-4 text-ink-muted" /> : <ChevronUp className="h-4 w-4 text-ink-muted" />}
      </button>
      {open ? (
        <div className="max-h-56 overflow-y-auto border-t border-line px-3 py-2">
          <div className="space-y-1">
            {snapshot.tasks.map((task) => <TaskRow key={task.task_id} task={task} />)}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function TaskCount({ value, label, danger = false }: { value: number; label: string; danger?: boolean }) {
  return (
    <span className={classNames("whitespace-nowrap text-[11.5px]", danger ? "text-danger" : "text-ink-muted")}>
      {value} {label}
    </span>
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
        <Icon
          className={classNames(
            "mt-0.5 h-3.5 w-3.5 shrink-0",
            statusTone(task.status),
            task.status === "in_progress" ? "animate-spin" : "",
          )}
        />
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

function taskProgress(tasks: SessionTaskNode[]): {
  completed: number;
  failed: number;
  inProgress: number;
  pending: number;
} {
  const leaves: SessionTaskNode[] = [];
  const visit = (task: SessionTaskNode) => {
    if (!task.children.length) leaves.push(task);
    else task.children.forEach(visit);
  };
  tasks.forEach(visit);
  return {
    completed: leaves.filter((task) => task.status === "completed" || task.status === "skipped").length,
    failed: leaves.filter((task) => task.status === "failed").length,
    inProgress: leaves.filter((task) => task.status === "in_progress" || task.status === "verifying").length,
    pending: leaves.filter((task) => task.status === "pending").length,
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
