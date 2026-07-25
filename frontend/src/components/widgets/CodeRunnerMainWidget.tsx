import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Box,
  CheckCircle2,
  Clock3,
  Loader2,
  Play,
  RotateCcw,
  Server,
  Square,
  TerminalSquare,
} from "lucide-react";
import { api, apiErrorMessage } from "@/lib/api";
import { classNames } from "@/lib/utils";
import type {
  SandboxExecution,
  SandboxExecutionLanguage,
  SandboxRecord,
  SandboxStatus,
} from "@/types";

const ACTOR = { user_id: "anonymous", tenant_id: "default" };

const EXAMPLES: Record<SandboxExecutionLanguage, string> = {
  python: [
    "from pathlib import Path",
    "",
    "counter_file = Path('counter.txt')",
    "counter = int(counter_file.read_text()) + 1 if counter_file.exists() else 1",
    "counter_file.write_text(str(counter))",
    "print(f'Hello from your sandbox. Run count: {counter}')",
  ].join("\n"),
  bash: [
    "set -e",
    "echo \"Workspace: $PWD\"",
    "python --version",
    "printf 'bash was here\\n' >> sandbox.log",
    "tail -n 3 sandbox.log",
  ].join("\n"),
  node: [
    "const fs = require('node:fs');",
    "const file = 'node-counter.txt';",
    "const count = fs.existsSync(file) ? Number(fs.readFileSync(file, 'utf8')) + 1 : 1;",
    "fs.writeFileSync(file, String(count));",
    "console.log(`Node sandbox run: ${count}`);",
  ].join("\n"),
  shell: "echo \"Hello from the sandbox shell\"",
};

export function CodeRunnerMainWidget() {
  const [sandbox, setSandbox] = useState<SandboxRecord | null>(null);
  const [executions, setExecutions] = useState<SandboxExecution[]>([]);
  const [selectedExecution, setSelectedExecution] = useState<SandboxExecution | null>(null);
  const [language, setLanguage] = useState<SandboxExecutionLanguage>("python");
  const [scripts, setScripts] = useState<Record<SandboxExecutionLanguage, string>>(EXAMPLES);
  const [timeoutSeconds, setTimeoutSeconds] = useState(60);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const loadHistory = useCallback(async () => {
    const history = await api.get<SandboxExecution[]>(
      `/sandboxes/executions?user_id=${ACTOR.user_id}&tenant_id=${ACTOR.tenant_id}`,
    );
    setExecutions(history);
    setSelectedExecution((current) => current ?? history[0] ?? null);
  }, []);

  const ensureSandbox = useCallback(async () => {
    const record = await api.post<SandboxRecord>("/sandboxes/ensure", ACTOR);
    setSandbox(record);
    return record;
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([ensureSandbox(), loadHistory()])
      .catch((error) => {
        if (!cancelled) setNotice({ tone: "error", text: apiErrorMessage(error) });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ensureSandbox, loadHistory]);

  async function runScript() {
    if (running || !scripts[language].trim()) return;
    setRunning(true);
    setNotice(null);
    setSandbox((current) => (current ? { ...current, status: "busy" } : current));
    try {
      const execution = await api.post<SandboxExecution>("/sandboxes/execute", {
        ...ACTOR,
        language,
        script: scripts[language],
        timeout_seconds: timeoutSeconds,
        working_directory: ".",
      });
      setSelectedExecution(execution);
      setExecutions((current) => [execution, ...current.filter((item) => item.id !== execution.id)]);
      setNotice({
        tone: execution.status === "succeeded" ? "success" : "error",
        text:
          execution.status === "succeeded"
            ? `执行成功，退出码 ${execution.exit_code ?? 0}`
            : execution.status === "timed_out"
              ? "执行超时，进程已终止"
              : `执行失败，退出码 ${execution.exit_code ?? "未知"}`,
      });
      await ensureSandbox();
    } catch (error) {
      setNotice({ tone: "error", text: apiErrorMessage(error) });
      await ensureSandbox().catch(() => undefined);
    } finally {
      setRunning(false);
    }
  }

  async function stopSandbox() {
    setNotice(null);
    try {
      const record = await api.post<SandboxRecord>("/sandboxes/stop", ACTOR);
      setSandbox(record);
      setNotice({ tone: "success", text: "运行容器已停止，工作区文件仍会保留。" });
    } catch (error) {
      setNotice({ tone: "error", text: apiErrorMessage(error) });
    }
  }

  async function resetSandbox() {
    if (!window.confirm("确定重置当前沙箱吗？工作区文件和执行历史都会被删除。")) return;
    setNotice(null);
    try {
      await api.delete(`/sandboxes/current?user_id=${ACTOR.user_id}&tenant_id=${ACTOR.tenant_id}`);
      setExecutions([]);
      setSelectedExecution(null);
      const record = await ensureSandbox();
      setNotice({ tone: "success", text: `沙箱 ${record.runtime_name} 已重建。` });
    } catch (error) {
      setNotice({ tone: "error", text: apiErrorMessage(error) });
    }
  }

  const status = sandbox?.status ?? "provisioning";
  const activeScript = scripts[language];
  const output = useMemo(() => selectedExecution ?? executions[0] ?? null, [executions, selectedExecution]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center bg-app-bg text-[12px] text-ink-muted">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />正在初始化用户沙箱…
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto bg-app-bg">
      <div className="mx-auto max-w-6xl space-y-3 p-4">
        <header className="rounded-lg border border-line bg-white p-3">
          <div className="flex flex-wrap items-start gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-soft text-accent">
              <TerminalSquare className="h-5 w-5" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-[14px] font-extrabold text-ink">代码运行器</h2>
                <StatusBadge status={status} />
              </div>
              <p className="mt-1 text-[11px] leading-relaxed text-ink-muted">
                每个用户使用独立工作区。Python、npm 等用户级依赖安装到工作区后可跨容器重启保留。
              </p>
            </div>
            <button type="button" onClick={() => void stopSandbox()} className="btn-outline h-8 text-[11px]">
              <Square className="h-3.5 w-3.5" />停止容器
            </button>
            <button type="button" onClick={() => void resetSandbox()} className="btn-danger-outline h-8 text-[11px]">
              <RotateCcw className="h-3.5 w-3.5" />重置环境
            </button>
          </div>
          {sandbox ? (
            <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
              <Info icon={Box} label="运行时" value={sandbox.runtime_name} mono />
              <Info icon={Server} label="隔离驱动" value={sandbox.driver === "kubernetes" ? "Kubernetes · gVisor" : "本地开发驱动"} />
              <Info icon={Clock3} label="工作区" value={sandbox.workspace} mono />
            </div>
          ) : null}
        </header>

        {notice ? (
          <div
            className={classNames(
              "flex items-center gap-2 rounded-lg border p-3 text-[12px] font-semibold",
              notice.tone === "success"
                ? "border-success/20 bg-success-soft text-success-deep"
                : "border-danger-ring bg-danger-soft text-danger-deep",
            )}
          >
            {notice.tone === "success" ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
            {notice.text}
          </div>
        ) : null}

        <section className="overflow-hidden rounded-lg border border-line bg-white">
          <div className="flex flex-wrap items-center gap-2 border-b border-line bg-app-soft px-3 py-2">
            {(["python", "bash", "node"] as SandboxExecutionLanguage[]).map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => setLanguage(item)}
                className={classNames(
                  "h-7 rounded-md px-3 font-mono text-[10.5px] font-bold",
                  language === item ? "bg-white text-accent shadow-sm" : "text-ink-muted hover:text-ink",
                )}
              >
                {item === "node" ? "Node.js" : item[0].toUpperCase() + item.slice(1)}
              </button>
            ))}
            <span className="flex-1" />
            <label className="flex items-center gap-1.5 text-[10.5px] font-bold text-ink-muted">
              超时
              <input
                type="number"
                min={1}
                max={300}
                value={timeoutSeconds}
                onChange={(event) => setTimeoutSeconds(Number(event.target.value))}
                className="input-soft h-7 w-16 bg-white px-2 text-[10.5px]"
              />
              秒
            </label>
            <button
              type="button"
              disabled={running || !activeScript.trim()}
              onClick={() => void runScript()}
              className="btn-primary h-8 text-[11px] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              {running ? "运行中…" : "运行脚本"}
            </button>
          </div>
          <textarea
            aria-label="脚本编辑器"
            spellCheck={false}
            value={activeScript}
            onChange={(event) => setScripts((current) => ({ ...current, [language]: event.target.value }))}
            className="min-h-[280px] w-full resize-y border-0 bg-slate-950 p-4 font-mono text-[12px] leading-6 text-slate-100 outline-none"
          />
        </section>

        <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.4fr)_minmax(300px,0.6fr)]">
          <OutputPanel execution={output} />
          <HistoryPanel executions={executions} selected={output?.id ?? null} onSelect={setSelectedExecution} />
        </div>
      </div>
    </div>
  );
}

function OutputPanel({ execution }: { execution: SandboxExecution | null }) {
  return (
    <section className="overflow-hidden rounded-lg border border-line bg-white">
      <header className="flex items-center gap-2 border-b border-line px-3 py-2.5">
        <TerminalSquare className="h-4 w-4 text-accent" />
        <h3 className="text-[12.5px] font-extrabold text-ink">输入与输出</h3>
        {execution ? (
          <>
            <ExecutionBadge status={execution.status} />
            <span className="text-[10px] text-ink-muted">{formatDuration(execution.duration_ms)}</span>
          </>
        ) : null}
      </header>
      {!execution ? (
        <div className="flex min-h-52 items-center justify-center text-[11.5px] text-ink-muted">
          运行脚本后在这里查看本次输入、标准输出和错误。
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 p-3 lg:grid-cols-2">
          <CodeBlock label={`${execution.language} 输入`} value={execution.script} />
          <div className="space-y-3">
            <CodeBlock label="标准输出" value={execution.stdout || "（无输出）"} />
            {execution.stderr ? <CodeBlock label="标准错误" value={execution.stderr} error /> : null}
            <p className="text-[10px] text-ink-subtle">
              退出码：{execution.exit_code ?? "—"} · 工作目录：/workspace/{execution.working_directory === "." ? "" : execution.working_directory}
              {execution.truncated ? " · 输出已截断" : ""}
            </p>
          </div>
        </div>
      )}
    </section>
  );
}

function HistoryPanel({
  executions,
  selected,
  onSelect,
}: {
  executions: SandboxExecution[];
  selected: string | null;
  onSelect: (execution: SandboxExecution) => void;
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-line bg-white">
      <header className="flex items-center gap-2 border-b border-line px-3 py-2.5">
        <Clock3 className="h-4 w-4 text-accent" />
        <h3 className="text-[12.5px] font-extrabold text-ink">执行历史</h3>
        <span className="rounded-md bg-app-soft px-1.5 py-0.5 text-[9.5px] font-bold text-ink-muted">{executions.length}</span>
      </header>
      <div className="max-h-[420px] overflow-y-auto p-2">
        {!executions.length ? (
          <div className="py-12 text-center text-[11.5px] text-ink-muted">暂无执行记录</div>
        ) : (
          <div className="space-y-1.5">
            {executions.map((execution) => (
              <button
                key={execution.id}
                type="button"
                onClick={() => onSelect(execution)}
                className={classNames(
                  "w-full rounded-lg border p-2.5 text-left",
                  selected === execution.id ? "border-accent-ring bg-accent-soft" : "border-line bg-white hover:bg-app-soft",
                )}
              >
                <div className="flex items-center gap-2">
                  <ExecutionBadge status={execution.status} />
                  <span className="font-mono text-[10.5px] font-bold text-ink">{execution.language}</span>
                  <span className="flex-1" />
                  <span className="text-[9.5px] text-ink-subtle">{formatDuration(execution.duration_ms)}</span>
                </div>
                <p className="mt-1.5 truncate font-mono text-[10px] text-ink-muted">
                  {execution.script.split(/\r?\n/, 1)[0]}
                </p>
                <p className="mt-1 text-[9.5px] text-ink-subtle">{formatTime(execution.started_at)}</p>
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function CodeBlock({ label, value, error = false }: { label: string; value: string; error?: boolean }) {
  return (
    <div>
      <p className="mb-1.5 text-[10.5px] font-bold text-ink-muted">{label}</p>
      <pre
        className={classNames(
          "max-h-64 min-h-24 overflow-auto whitespace-pre-wrap rounded-lg p-3 font-mono text-[10.5px] leading-relaxed",
          error ? "bg-danger-soft text-danger-deep" : "bg-slate-950 text-slate-100",
        )}
      >
        {value}
      </pre>
    </div>
  );
}

function StatusBadge({ status }: { status: SandboxStatus }) {
  const label: Record<SandboxStatus, string> = {
    provisioning: "初始化中",
    ready: "就绪",
    busy: "运行中",
    stopped: "已停止",
    error: "异常",
  };
  const style: Record<SandboxStatus, string> = {
    provisioning: "bg-warning-soft text-warning-deep",
    ready: "bg-success-soft text-success-deep",
    busy: "bg-accent-soft text-accent",
    stopped: "bg-app-soft text-ink-muted",
    error: "bg-danger-soft text-danger-deep",
  };
  return <span className={classNames("rounded-md px-1.5 py-0.5 text-[9.5px] font-bold", style[status])}>{label[status]}</span>;
}

function ExecutionBadge({ status }: { status: SandboxExecution["status"] }) {
  const label = { running: "运行中", succeeded: "成功", failed: "失败", timed_out: "超时" }[status];
  const style = {
    running: "bg-warning-soft text-warning-deep",
    succeeded: "bg-success-soft text-success-deep",
    failed: "bg-danger-soft text-danger-deep",
    timed_out: "bg-danger-soft text-danger-deep",
  }[status];
  return <span className={classNames("rounded-md px-1.5 py-0.5 text-[9.5px] font-bold", style)}>{label}</span>;
}

function Info({
  icon: Icon,
  label,
  value,
  mono = false,
}: {
  icon: typeof Box;
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-center gap-2 rounded-lg bg-app-soft p-2.5">
      <Icon className="h-3.5 w-3.5 text-ink-subtle" />
      <div className="min-w-0">
        <p className="text-[9.5px] font-bold text-ink-subtle">{label}</p>
        <p className={classNames("mt-0.5 truncate text-[10.5px] text-ink", mono && "font-mono")}>{value}</p>
      </div>
    </div>
  );
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function formatDuration(value?: number | null): string {
  if (value == null) return "—";
  return value < 1_000 ? `${Math.round(value)} ms` : `${(value / 1_000).toFixed(2)} s`;
}
