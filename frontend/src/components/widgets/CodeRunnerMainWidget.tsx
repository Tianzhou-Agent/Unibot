import { useTranslation } from "react-i18next";
import { currentLocale } from "@/i18n";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Box,
  CheckCircle2,
  Clock3,
  FileCode2,
  Loader2,
  Play,
  RotateCcw,
  Server,
  Square,
  TerminalSquare,
} from "lucide-react";
import AceEditor from "react-ace";
import "ace-builds/src-noconflict/mode-javascript";
import "ace-builds/src-noconflict/mode-python";
import "ace-builds/src-noconflict/mode-sh";
import "ace-builds/src-noconflict/theme-one_dark";
import { api, apiErrorMessage } from "@/lib/api";
import { useMockSession } from "@/lib/mockSession";
import { classNames } from "@/lib/utils";
import type {
  SandboxExecution,
  SandboxExecutionLanguage,
  SandboxRecord,
  SandboxStatus,
} from "@/types";

const EDITOR_LANGUAGE: Record<SandboxExecutionLanguage, { file: string; label: string; mode: string }> = {
  python: { file: "main.py", label: "Python", mode: "python" },
  bash: { file: "script.sh", label: "Bash", mode: "sh" },
  node: { file: "index.js", label: "Node.js", mode: "javascript" },
  shell: { file: "script.ps1", label: "Shell", mode: "sh" },
};

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

export function CodeRunnerMainWidget({ workspaceId }: { workspaceId?: string | null }) {
  const { t } = useTranslation("codeRunner");
  const { profile } = useMockSession();
  const scope = useMemo<Record<string, string>>(
    () => ({
      user_id: profile.actorUserId,
      tenant_id: profile.tenantId,
      ...(workspaceId ? { workspace_id: workspaceId } : {}),
    }),
    [profile.actorUserId, profile.tenantId, workspaceId],
  );
  const scopeQuery = useMemo(() => new URLSearchParams(scope).toString(), [scope]);
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
      `/sandboxes/executions?${scopeQuery}`,
    );
    setExecutions(history);
    setSelectedExecution((current) => current ?? history[0] ?? null);
  }, [scopeQuery]);

  const ensureSandbox = useCallback(async () => {
    const record = await api.post<SandboxRecord>("/sandboxes/ensure", scope);
    setSandbox(record);
    return record;
  }, [scope]);

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
        ...scope,
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
            ? t("ok", { code: execution.exit_code ?? 0 })
            : execution.status === "timed_out"
              ? t("timeout")
              : t("fail", { code: execution.exit_code ?? t("unknown") }),
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
      const record = await api.post<SandboxRecord>("/sandboxes/stop", scope);
      setSandbox(record);
      setNotice({ tone: "success", text: t("stopped") });
    } catch (error) {
      setNotice({ tone: "error", text: apiErrorMessage(error) });
    }
  }

  async function resetSandbox() {
    const confirmed = window.confirm(
      workspaceId
        ? t("resetWs")
        : t("resetSandbox"),
    );
    if (!confirmed) return;
    setNotice(null);
    try {
      await api.delete(`/sandboxes/current?${scopeQuery}`);
      setExecutions([]);
      setSelectedExecution(null);
      const record = await ensureSandbox();
      setNotice({
        tone: "success",
        text: workspaceId
          ? t("rebuiltWs", { name: record.runtime_name })
          : t("rebuiltSandbox", { name: record.runtime_name }),
      });
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
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />{t("init")}
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
                <h2 className="text-[14px] font-extrabold text-ink">{t("title")}</h2>
                <StatusBadge status={status} />
              </div>
              <p className="mt-1 text-[11px] leading-relaxed text-ink-muted">
                {workspaceId
                  ? t("descWs")
                  : t("descUser")}
              </p>
            </div>
            <button type="button" onClick={() => void stopSandbox()} className="btn-outline h-8 text-[11px]">
              <Square className="h-3.5 w-3.5" />{t("stop")}
            </button>
            <button type="button" onClick={() => void resetSandbox()} className="btn-danger-outline h-8 text-[11px]">
              <RotateCcw className="h-3.5 w-3.5" />{t("reset")}
            </button>
          </div>
          {sandbox ? (
            <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
              <Info icon={Box} label={t("info.runtime")} value={sandbox.runtime_name} mono />
              <Info icon={Server} label={t("info.driver")} value={sandbox.driver === "kubernetes" ? "Kubernetes · gVisor" : t("info.driverLocal")} />
              <Info icon={Clock3} label={t("info.workspace")} value={sandbox.workspace} mono />
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

        <section className="overflow-hidden rounded-lg border border-slate-700 bg-slate-950 shadow-card">
          <div className="flex items-end gap-1 bg-slate-900 px-2 pt-2">
            {(["python", "bash", "node"] as SandboxExecutionLanguage[]).map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => setLanguage(item)}
                className={classNames(
                  "flex h-8 items-center gap-1.5 rounded-t-md border border-b-0 px-3 font-mono text-[10.5px] font-bold transition-colors",
                  language === item
                    ? "border-slate-700 bg-[#282c34] text-slate-100"
                    : "border-transparent text-slate-400 hover:bg-slate-800 hover:text-slate-200",
                )}
              >
                <FileCode2 className="h-3.5 w-3.5" />
                {EDITOR_LANGUAGE[item].file}
              </button>
            ))}
            <span className="ml-auto pb-2 pr-2 font-mono text-[9.5px] text-slate-500">
              {sandbox?.runtime_name ?? "sandbox"}
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2 border-y border-slate-700 bg-slate-800 px-3 py-2">
            <span className="font-mono text-[10px] font-bold text-slate-300">
              {EDITOR_LANGUAGE[language].label}
            </span>
            <span className="flex-1" />
            <label className="flex items-center gap-1.5 text-[10.5px] font-bold text-slate-300">
              {t("timeoutLabel")}
              <input
                type="number"
                min={1}
                max={300}
                value={timeoutSeconds}
                onChange={(event) => setTimeoutSeconds(Number(event.target.value))}
                className="h-7 w-16 rounded-md border border-slate-600 bg-slate-900 px-2 text-[10.5px] text-slate-100 outline-none focus:border-accent"
              />
              {t("seconds")}
            </label>
            <button
              type="button"
              disabled={running || !activeScript.trim()}
              onClick={() => void runScript()}
              className="btn-primary h-8 text-[11px] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              {running ? t("running") : t("run")}
            </button>
          </div>
          <div data-testid="script-editor" className="bg-[#282c34]">
            <AceEditor
              name="unibot-script-editor"
              mode={EDITOR_LANGUAGE[language].mode}
              theme="one_dark"
              width="100%"
              height="380px"
              fontSize={13}
              lineHeight={21}
              showGutter
              showPrintMargin={false}
              highlightActiveLine
              wrapEnabled={false}
              value={activeScript}
              onLoad={(editor) => {
                editor.setOption("textInputAriaLabel", t("editorAria"));
                editor.textInput.setAriaLabel();
              }}
              onChange={(value) => setScripts((current) => ({ ...current, [language]: value }))}
              setOptions={{
                displayIndentGuides: true,
                enableBasicAutocompletion: false,
                enableLiveAutocompletion: false,
                highlightSelectedWord: true,
                showFoldWidgets: true,
                showLineNumbers: true,
                tabSize: 2,
                textInputAriaLabel: t("editorAria"),
                useSoftTabs: true,
                useWorker: false,
              }}
              editorProps={{ $blockScrolling: true }}
            />
          </div>
          <div className="flex items-center gap-3 border-t border-slate-700 bg-accent px-3 py-1 text-[9.5px] font-semibold text-white">
            <span>{EDITOR_LANGUAGE[language].file}</span>
            <span className="ml-auto">Ln {activeScript.split(/\r?\n/).length}</span>
            <span>UTF-8</span>
            <span>LF</span>
            <span>{EDITOR_LANGUAGE[language].label}</span>
          </div>
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
  const { t } = useTranslation("codeRunner");
  return (
    <section className="overflow-hidden rounded-lg border border-line bg-white">
      <header className="flex items-center gap-2 border-b border-line px-3 py-2.5">
        <TerminalSquare className="h-4 w-4 text-accent" />
        <h3 className="text-[12.5px] font-extrabold text-ink">{t("output")}</h3>
        {execution ? (
          <>
            <ExecutionBadge status={execution.status} />
            <span className="text-[10px] text-ink-muted">{formatDuration(execution.duration_ms)}</span>
          </>
        ) : null}
      </header>
      {!execution ? (
        <div className="flex min-h-52 items-center justify-center text-[11.5px] text-ink-muted">
          {t("emptyOutput")}
        </div>
      ) : (
        <div className="space-y-3 p-3">
          <CodeBlock label={t("stdout")} value={execution.stdout || t("noOutput")} />
          {execution.stderr ? <CodeBlock label={t("stderr")} value={execution.stderr} error /> : null}
          <p className="text-[10px] text-ink-subtle">
            {t("exitCode", { code: execution.exit_code ?? "—", dir: execution.working_directory === "." ? "" : execution.working_directory })}
            {execution.truncated ? t("truncated") : ""}
          </p>
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
  const { t } = useTranslation("codeRunner");
  return (
    <section className="overflow-hidden rounded-lg border border-line bg-white">
      <header className="flex items-center gap-2 border-b border-line px-3 py-2.5">
        <Clock3 className="h-4 w-4 text-accent" />
        <h3 className="text-[12.5px] font-extrabold text-ink">{t("history")}</h3>
        <span className="rounded-md bg-app-soft px-1.5 py-0.5 text-[9.5px] font-bold text-ink-muted">{executions.length}</span>
      </header>
      <div className="max-h-[420px] overflow-y-auto p-2">
        {!executions.length ? (
          <div className="py-12 text-center text-[11.5px] text-ink-muted">{t("noHistory")}</div>
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
  const { t } = useTranslation("codeRunner");
  const label: Record<SandboxStatus, string> = {
    provisioning: t("sandbox.provisioning"),
    ready: t("sandbox.ready"),
    busy: t("sandbox.busy"),
    stopped: t("sandbox.stopped"),
    error: t("sandbox.error"),
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
  const { t } = useTranslation("codeRunner");
  const label = t(`exec.${status}`);
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
  return new Intl.DateTimeFormat(currentLocale(), {
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
