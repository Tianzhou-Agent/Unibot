import i18n, { currentLocale } from "@/i18n";
import { Trans, useTranslation } from "react-i18next";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertCircle,
  CalendarClock,
  CheckCircle2,
  Clock3,
  History,
  Loader2,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import { Link } from "react-router-dom";
import { Topbar } from "@/components/layout/Topbar";
import { api, apiErrorMessage } from "@/lib/api";
import { classNames } from "@/lib/utils";
import type {
  AinaInstallation,
  AinaRecord,
  ScheduleType,
  ScheduledAinaExecution,
  ScheduledAinaStatus,
  ScheduledAinaTask,
} from "@/types";

interface ScheduleForm {
  ainaId: string;
  name: string;
  scheduleType: ScheduleType;
  intervalSeconds: string;
  cronExpression: string;
  timezone: string;
  prompt: string;
  enabled: boolean;
}

const DEFAULT_FORM: ScheduleForm = {
  ainaId: "",
  name: "",
  scheduleType: "interval",
  intervalSeconds: "3600",
  cronExpression: "0 9 * * *",
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  prompt: "",
  enabled: true,
};

export function ScheduledAinaMainWidget() {
  return <ScheduledAinaPage embedded />;
}

export default function ScheduledAinaPage({ embedded = false }: { embedded?: boolean }) {
  const { t } = useTranslation("schedule");
  const [tasks, setTasks] = useState<ScheduledAinaTask[]>([]);
  const [ainas, setAinas] = useState<AinaRecord[]>([]);
  const [installations, setInstallations] = useState<AinaInstallation[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<ScheduleForm>(DEFAULT_FORM);
  const [detailTaskId, setDetailTaskId] = useState<string | null>(null);
  const [detailMode, setDetailMode] = useState<"history" | "debug">("history");
  const [executions, setExecutions] = useState<Record<string, ScheduledAinaExecution[]>>({});
  const [historyLoadingId, setHistoryLoadingId] = useState<string | null>(null);
  const [debugPrompts, setDebugPrompts] = useState<Record<string, string>>({});
  const [runningId, setRunningId] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [taskData, ainaData, installationData] = await Promise.all([
        api.get<ScheduledAinaTask[]>("/aina-schedules"),
        api.get<AinaRecord[]>("/ainas"),
        api.get<AinaInstallation[]>("/installations?user_id=anonymous&tenant_id=default"),
      ]);
      setTasks(taskData);
      setAinas(ainaData);
      setInstallations(installationData);
    } catch (error) {
      setNotice({ tone: "error", text: apiErrorMessage(error) });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const installedIds = useMemo(
    () => new Set(installations.filter((item) => item.status === "active").map((item) => item.aina_id)),
    [installations],
  );
  const runnableAinas = useMemo(
    () =>
      ainas.filter(
        (item) => item.manifest.runtime.type === "remote" && installedIds.has(item.manifest.aina.id),
      ),
    [ainas, installedIds],
  );
  const ainaNames = useMemo(
    () => new Map(ainas.map((item) => [item.manifest.aina.id, item.manifest.aina.name])),
    [ainas],
  );

  function openCreate() {
    setEditingId(null);
    setForm({ ...DEFAULT_FORM, ainaId: runnableAinas[0]?.manifest.aina.id ?? "" });
    setEditorOpen(true);
    setNotice(null);
  }

  function openEdit(task: ScheduledAinaTask) {
    setEditingId(task.id);
    setForm({
      ainaId: task.aina_id,
      name: task.name,
      scheduleType: task.schedule_type,
      intervalSeconds: String(task.interval_seconds),
      cronExpression: task.cron_expression ?? "0 9 * * *",
      timezone: task.timezone,
      prompt: task.prompt ?? ainaInputText(task.input),
      enabled: task.enabled,
    });
    setEditorOpen(true);
    setNotice(null);
  }

  async function save() {
    setSaving(true);
    setNotice(null);
    try {
      const prompt = form.prompt.trim();
      const payload = {
        name: form.name.trim(),
        schedule_type: form.scheduleType,
        interval_seconds: Number(form.intervalSeconds),
        cron_expression: form.scheduleType === "cron" ? form.cronExpression.trim() : undefined,
        timezone: form.timezone.trim(),
        prompt,
        enabled: form.enabled,
      };
      if (!payload.name) throw new Error(t("err.name"));
      if (!payload.prompt) throw new Error(t("err.prompt"));
      if (form.scheduleType === "interval" && (!Number.isFinite(payload.interval_seconds) || payload.interval_seconds < 10)) {
        throw new Error(t("err.interval"));
      }
      if (editingId) {
        await api.patch<ScheduledAinaTask>(`/aina-schedules/${editingId}`, payload);
      } else {
        if (!form.ainaId) throw new Error(t("err.aina"));
        await api.post<ScheduledAinaTask>("/aina-schedules", {
          ...payload,
          aina_id: form.ainaId,
          user_id: "anonymous",
          tenant_id: "default",
        });
      }
      setEditorOpen(false);
      setNotice({ tone: "success", text: editingId ? t("notice.updated") : t("notice.created") });
      await load();
    } catch (error) {
      setNotice({ tone: "error", text: apiErrorMessage(error) });
    } finally {
      setSaving(false);
    }
  }

  async function toggleEnabled(task: ScheduledAinaTask) {
    try {
      const updated = await api.patch<ScheduledAinaTask>(`/aina-schedules/${task.id}`, {
        enabled: !task.enabled,
      });
      replaceTask(updated);
      setNotice({ tone: "success", text: updated.enabled ? t("notice.enabled") : t("notice.paused") });
    } catch (error) {
      setNotice({ tone: "error", text: apiErrorMessage(error) });
    }
  }

  async function remove(task: ScheduledAinaTask) {
    if (!window.confirm(t("confirm.delete", { name: task.name }))) return;
    try {
      await api.delete(`/aina-schedules/${task.id}`);
      setTasks((current) => current.filter((item) => item.id !== task.id));
      setNotice({ tone: "success", text: t("notice.deleted") });
    } catch (error) {
      setNotice({ tone: "error", text: apiErrorMessage(error) });
    }
  }

  async function openDebug(task: ScheduledAinaTask) {
    setDetailTaskId(task.id);
    setDetailMode("debug");
    setDebugPrompts((current) => ({
      ...current,
      [task.id]: current[task.id] ?? task.prompt ?? ainaInputText(task.input),
    }));
    setNotice(null);
    await loadExecutions(task.id);
  }

  async function openHistory(task: ScheduledAinaTask) {
    setDetailTaskId(task.id);
    setDetailMode("history");
    setNotice(null);
    await loadExecutions(task.id);
  }

  async function loadExecutions(taskId: string) {
    setHistoryLoadingId(taskId);
    try {
      const data = await api.get<ScheduledAinaExecution[]>(
        `/aina-schedules/${taskId}/executions?limit=50`,
      );
      setExecutions((current) => ({ ...current, [taskId]: data }));
    } catch (error) {
      setNotice({ tone: "error", text: apiErrorMessage(error) });
    } finally {
      setHistoryLoadingId(null);
    }
  }

  async function runDebug(task: ScheduledAinaTask) {
    setRunningId(task.id);
    setNotice(null);
    try {
      const prompt = (debugPrompts[task.id] ?? "").trim();
      if (!prompt) throw new Error(t("err.debugPrompt"));
      const updated = await api.post<ScheduledAinaTask>(`/aina-schedules/${task.id}/run`, { prompt });
      replaceTask(updated);
      await loadExecutions(task.id);
      setNotice({
        tone: updated.last_status === "succeeded" ? "success" : "error",
        text: updated.last_status === "succeeded" ? t("notice.debugOk") : updated.last_error ?? t("notice.debugFail"),
      });
    } catch (error) {
      setNotice({ tone: "error", text: apiErrorMessage(error) });
    } finally {
      setRunningId(null);
    }
  }

  function replaceTask(updated: ScheduledAinaTask) {
    setTasks((current) => current.map((item) => (item.id === updated.id ? updated : item)));
  }

  const actions = (
    <div className="flex items-center gap-2">
      <button type="button" onClick={() => void load()} className="btn-outline h-8" aria-label={t("refreshAria")}>
        <RefreshCw className="h-3.5 w-3.5" />{t("refresh")}
      </button>
      <button type="button" onClick={openCreate} className="btn-primary h-8">
        <Plus className="h-3.5 w-3.5" />{t("newTask")}
      </button>
    </div>
  );

  return (
    <div className={classNames("flex h-full min-h-0 flex-col", embedded ? "bg-white" : "bg-app-bg")}>
      {embedded ? (
        <header className="flex min-h-14 flex-wrap items-center gap-3 border-b border-line bg-white px-3 py-2">
          <div className="flex min-w-0 flex-1 items-center gap-2.5">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent">
              <CalendarClock className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <h2 className="truncate text-[13.5px] font-extrabold text-ink">{t("header")}</h2>
              <p className="truncate text-[10.5px] text-ink-muted">{t("headerMeta", { count: tasks.length })}</p>
            </div>
          </div>
          {actions}
        </header>
      ) : (
        <Topbar
        title={t("topTitle")}
        badge={{ label: t("badge", { count: tasks.length }), tone: "neutral" }}
        actions={actions}
        />
      )}
      <div className={classNames("min-h-0 flex-1 overflow-y-auto", embedded ? "p-3" : "p-4")}>
        <div className={classNames("mx-auto max-w-6xl", embedded ? "space-y-3" : "space-y-4")}>
          {!embedded ? <section className="rounded-xl border border-line bg-white p-4 shadow-card">
            <div className="flex items-start gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <CalendarClock className="h-5 w-5" />
              </div>
              <div>
                <h1 className="text-[15px] font-extrabold text-ink">{t("heroTitle")}</h1>
                <p className="mt-1 text-[12px] leading-relaxed text-ink-muted">
                  {t("heroBody")}
                </p>
              </div>
            </div>
          </section> : null}

          {notice ? <Notice {...notice} onClose={() => setNotice(null)} /> : null}

          {editorOpen ? (
            <ScheduleEditor
              form={form}
              editing={editingId !== null}
              saving={saving}
              ainas={runnableAinas}
              onChange={setForm}
              onClose={() => setEditorOpen(false)}
              onSave={() => void save()}
            />
          ) : null}

          {!loading && runnableAinas.length === 0 ? (
            <div className="rounded-lg border border-warning/30 bg-warning-soft p-3 text-[11.5px] text-warning-deep">
              <Trans t={t} i18nKey="noRunnable" components={{ link: <Link to="/plugin" className="font-bold underline" /> }} />
            </div>
          ) : null}

          {loading ? <LoadingCards /> : null}
          {!loading && tasks.length === 0 ? (
            <div className="flex min-h-56 flex-col items-center justify-center rounded-lg border border-dashed border-line-strong bg-white px-4 text-center">
              <Clock3 className="h-7 w-7 text-ink-subtle" />
              <h2 className="mt-2 text-[12px] font-bold text-ink">{t("emptyTitle")}</h2>
              <p className="mt-1 text-[12px] text-ink-muted">{t("emptyBody")}</p>
            </div>
          ) : null}
          {!loading && tasks.length > 0 ? (
            <div className="space-y-3">
              {tasks.map((task) => (
                <TaskCard
                  key={task.id}
                  task={task}
                  ainaName={ainaNames.get(task.aina_id) ?? task.aina_id}
                  detailMode={detailTaskId === task.id ? detailMode : null}
                  executions={executions[task.id] ?? []}
                  historyLoading={historyLoadingId === task.id}
                  debugPrompt={debugPrompts[task.id] ?? ""}
                  running={runningId === task.id}
                  onDebugPrompt={(value) => setDebugPrompts((current) => ({ ...current, [task.id]: value }))}
                  onOpenDebug={() => void openDebug(task)}
                  onOpenHistory={() => void openHistory(task)}
                  onCloseDetail={() => setDetailTaskId(null)}
                  onRunDebug={() => void runDebug(task)}
                  onEdit={() => openEdit(task)}
                  onToggle={() => void toggleEnabled(task)}
                  onDelete={() => void remove(task)}
                />
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ScheduleEditor({
  form,
  editing,
  saving,
  ainas,
  onChange,
  onClose,
  onSave,
}: {
  form: ScheduleForm;
  editing: boolean;
  saving: boolean;
  ainas: AinaRecord[];
  onChange: (form: ScheduleForm) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  const { t } = useTranslation("schedule");
  const change = <K extends keyof ScheduleForm>(key: K, value: ScheduleForm[K]) =>
    onChange({ ...form, [key]: value });
  return (
    <section className="overflow-hidden rounded-lg border border-accent-ring bg-white">
      <div className="flex min-h-11 items-center gap-2 border-b border-line bg-accent-soft px-3 py-1.5">
        <CalendarClock className="h-4 w-4 text-accent" />
        <h2 className="text-[13px] font-extrabold text-ink">{editing ? t("editor.edit") : t("editor.new")}</h2>
        <span className="flex-1" />
        <button type="button" onClick={onClose} className="btn-ghost h-8" aria-label={t("editor.close")}><X className="h-4 w-4" /></button>
      </div>
      <div className="grid grid-cols-1 gap-3 p-3 lg:grid-cols-2">
        <label className="space-y-1.5 text-[11.5px] font-bold text-ink-muted">
          {t("editor.aina")}
          <select value={form.ainaId} disabled={editing} onChange={(event) => change("ainaId", event.target.value)} className="input-soft w-full text-[11.5px] disabled:opacity-60">
            <option value="">{t("editor.ainaPlaceholder")}</option>
            {ainas.map((item) => <option key={item.manifest.aina.id} value={item.manifest.aina.id}>{item.manifest.aina.name}</option>)}
          </select>
        </label>
        <label className="space-y-1.5 text-[11.5px] font-bold text-ink-muted">
          {t("editor.name")}
          <input value={form.name} onChange={(event) => change("name", event.target.value)} className="input-soft w-full text-[11.5px]" placeholder={t("editor.namePlaceholder")} />
        </label>
        <label className="space-y-1.5 text-[11.5px] font-bold text-ink-muted">
          {t("editor.scheduleType")}
          <select value={form.scheduleType} onChange={(event) => change("scheduleType", event.target.value as ScheduleType)} className="input-soft w-full text-[11.5px]">
            <option value="interval">{t("editor.interval")}</option>
            <option value="cron">{t("editor.cron")}</option>
          </select>
        </label>
        {form.scheduleType === "interval" ? (
          <label className="space-y-1.5 text-[11.5px] font-bold text-ink-muted">
            {t("editor.intervalSeconds")}
            <input type="number" min={10} value={form.intervalSeconds} onChange={(event) => change("intervalSeconds", event.target.value)} className="input-soft w-full text-[11.5px]" />
          </label>
        ) : (
          <label className="space-y-1.5 text-[11.5px] font-bold text-ink-muted">
            {t("editor.cron5")}
            <input value={form.cronExpression} onChange={(event) => change("cronExpression", event.target.value)} className="input-soft w-full font-mono text-[11.5px]" placeholder="0 9 * * 1-5" />
          </label>
        )}
        <label className="space-y-1.5 text-[11.5px] font-bold text-ink-muted">
          {t("editor.timezone")}
          <input value={form.timezone} onChange={(event) => change("timezone", event.target.value)} className="input-soft w-full font-mono text-[11.5px]" placeholder="Asia/Shanghai" />
        </label>
        <label className="flex items-end gap-2 pb-2 text-[12px] font-bold text-ink-muted">
          <input type="checkbox" checked={form.enabled} onChange={(event) => change("enabled", event.target.checked)} className="h-4 w-4 accent-accent" />
          {t("editor.enableNow")}
        </label>
        <label className="space-y-1.5 text-[11.5px] font-bold text-ink-muted lg:col-span-2">
          {t("editor.prompt")}
          <textarea
            value={form.prompt}
            onChange={(event) => change("prompt", event.target.value)}
            rows={5}
            placeholder={t("editor.promptPlaceholder")}
            className="input-soft resize-y bg-white text-[12px] leading-relaxed"
          />
          <span className="block text-[10px] font-normal text-ink-subtle">{t("editor.promptHint")}</span>
        </label>
      </div>
      <div className="flex items-center justify-end gap-2 border-t border-line px-3 py-2.5">
        <button type="button" onClick={onClose} className="btn-outline h-8 text-[11px]">{t("editor.cancel")}</button>
        <button type="button" disabled={saving} onClick={onSave} className="btn-primary h-8 text-[11px]">
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}{saving ? t("editor.saving") : t("editor.save")}
        </button>
      </div>
    </section>
  );
}

function TaskCard({
  task,
  ainaName,
  detailMode,
  executions,
  historyLoading,
  debugPrompt,
  running,
  onDebugPrompt,
  onOpenDebug,
  onOpenHistory,
  onCloseDetail,
  onRunDebug,
  onEdit,
  onToggle,
  onDelete,
}: {
  task: ScheduledAinaTask;
  ainaName: string;
  detailMode: "history" | "debug" | null;
  executions: ScheduledAinaExecution[];
  historyLoading: boolean;
  debugPrompt: string;
  running: boolean;
  onDebugPrompt: (value: string) => void;
  onOpenDebug: () => void;
  onOpenHistory: () => void;
  onCloseDetail: () => void;
  onRunDebug: () => void;
  onEdit: () => void;
  onToggle: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation("schedule");
  return (
    <article className="overflow-hidden rounded-lg border border-line bg-white">
      <div className="p-3">
        <div className="flex flex-wrap items-start gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent-soft text-accent">
            <Clock3 className="h-4 w-4" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-[13px] font-extrabold text-ink">{task.name}</h2>
              <Status status={task.last_status} />
              <span className={classNames("rounded-md px-1.5 py-0.5 text-[9.5px] font-bold", task.enabled ? "bg-success-soft text-success-deep" : "bg-app-soft text-ink-muted")}>
                {task.enabled ? t("task.enabled") : t("task.paused")}
              </span>
            </div>
            <p className="mt-1 font-mono text-[10.5px] text-ink-subtle">{ainaName} · {scheduleLabel(task)}</p>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <button type="button" onClick={onOpenHistory} className="btn-outline h-8 px-2.5 text-[11px]"><History className="h-3.5 w-3.5" />{t("task.history")}</button>
            <button type="button" onClick={onOpenDebug} className="btn-primary h-8 px-2.5 text-[11px]"><Play className="h-3.5 w-3.5" />{t("task.debug")}</button>
            <button type="button" onClick={onEdit} className="btn-outline h-8 px-2.5 text-[11px]"><Pencil className="h-3.5 w-3.5" />{t("task.edit")}</button>
            <button type="button" onClick={onToggle} className="btn-outline h-8 px-2.5 text-[11px]">{task.enabled ? t("task.pause") : t("task.enable")}</button>
            <button type="button" onClick={onDelete} className="btn-danger-outline h-8 w-8 p-0" aria-label={t("task.deleteAria", { name: task.name })}><Trash2 className="h-3.5 w-3.5" /></button>
          </div>
        </div>
        <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-3">
          <Info label={t("task.nextRun")} value={task.enabled ? formatTime(task.next_run_at) : t("task.paused")} />
          <Info label={t("task.lastRun")} value={task.last_run_at ? formatTime(task.last_run_at) : t("task.neverRun")} />
          <Info label={t("task.node")} value={task.last_node_id ?? "—"} mono />
        </div>
        {task.last_error ? <div className="mt-3 rounded-lg border border-danger-ring bg-danger-soft p-2.5 text-[11.5px] text-danger-deep">{task.last_error}</div> : null}
      </div>
      {detailMode ? (
        <div className="border-t border-line bg-app-soft p-3">
          <div className="flex items-center gap-2">
            {detailMode === "history" ? <History className="h-4 w-4 text-accent" /> : <Activity className="h-4 w-4 text-accent" />}
            <h3 className="text-[12.5px] font-extrabold text-ink">
              {detailMode === "history" ? t("detail.history") : t("detail.debug", { name: ainaName })}
            </h3>
            {detailMode === "debug" ? <span className="text-[10.5px] text-ink-muted">{t("detail.debugHint")}</span> : null}
            <span className="flex-1" />
            <button type="button" onClick={onCloseDetail} className="btn-ghost h-7" aria-label={t("detail.closeAria")}><X className="h-4 w-4" /></button>
          </div>
          {detailMode === "history" ? (
            <ExecutionHistory executions={executions} loading={historyLoading} />
          ) : (
            <>
              <label className="mt-3 block text-[10.5px] font-bold text-ink-muted">
                {t("detail.promptFor", { name: ainaName })}
                <textarea
                  value={debugPrompt}
                  onChange={(event) => onDebugPrompt(event.target.value)}
                  rows={5}
                  aria-label={t("detail.debugAria", { name: ainaName })}
                  placeholder={t("detail.debugPlaceholder")}
                  className="input-soft mt-1.5 resize-y bg-white text-[12px] leading-relaxed"
                />
              </label>
              <div className="mt-3 flex items-center justify-end">
                <button type="button" disabled={running} onClick={onRunDebug} className="btn-primary h-8 text-[11px]">
                  {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                  {running ? t("detail.running") : t("detail.run")}
                </button>
              </div>
              <AinaDebugResult
                ainaName={ainaName}
                execution={executions.find((execution) => execution.trigger === "manual") ?? null}
                loading={historyLoading}
              />
            </>
          )}
        </div>
      ) : null}
    </article>
  );
}

function AinaDebugResult({
  ainaName,
  execution,
  loading,
}: {
  ainaName: string;
  execution: ScheduledAinaExecution | null;
  loading: boolean;
}) {
  const { t } = useTranslation("schedule");
  if (loading) {
    return (
      <div className="mt-3 flex items-center justify-center gap-2 rounded-lg border border-line bg-white py-8 text-[11.5px] text-ink-muted">
        <Loader2 className="h-4 w-4 animate-spin" />{t("debug.loading")}
      </div>
    );
  }
  if (!execution) {
    return (
      <div className="mt-3 rounded-lg border border-dashed border-line-strong bg-white py-8 text-center text-[11.5px] text-ink-muted">
        {t("debug.empty")}
      </div>
    );
  }

  const outputs = ainaOutputs(execution.result);
  return (
    <section className="mt-3 overflow-hidden rounded-lg border border-line bg-white">
      <header className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2.5">
        <Status status={execution.status} />
        <span className="text-[11.5px] font-bold text-ink">{t("debug.latest")}</span>
        <span className="text-[10.5px] text-ink-muted">{formatTime(execution.started_at)} · {formatDuration(execution.duration_ms)}</span>
        <span className="flex-1" />
        <span className="font-mono text-[10px] text-ink-subtle">{execution.node_id}</span>
      </header>
      <div className="grid grid-cols-1 gap-3 p-3 xl:grid-cols-2">
        <TextBlock label={t("debug.input")} value={ainaInputText(execution.input)} />
        <div>
          <p className="mb-1.5 text-[10.5px] font-bold text-ink-muted">{t("debug.output", { name: ainaName })}</p>
          {execution.error ? (
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-danger-soft p-3 font-mono text-[10.5px] leading-relaxed text-danger-deep">{execution.error}</pre>
          ) : outputs.length ? (
            <div className="space-y-2">
              {outputs.map((output, index) => (
                <AinaOutputValue key={`${output.type}-${index}`} output={output} />
              ))}
            </div>
          ) : (
            <pre className="max-h-64 overflow-auto rounded-lg bg-slate-950 p-3 font-mono text-[10.5px] leading-relaxed text-slate-100">{JSON.stringify(execution.result ?? {}, null, 2)}</pre>
          )}
        </div>
      </div>
    </section>
  );
}

function AinaOutputValue({ output }: { output: { type: string; content: unknown } }) {
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-app-soft">
      <div className="border-b border-line px-2.5 py-1.5 text-[9.5px] font-bold uppercase tracking-wide text-ink-subtle">{output.type}</div>
      {typeof output.content === "string" ? (
        <div className="whitespace-pre-wrap px-3 py-2.5 text-[11.5px] leading-relaxed text-ink">{output.content}</div>
      ) : (
        <pre className="max-h-56 overflow-auto bg-slate-950 p-3 font-mono text-[10.5px] leading-relaxed text-slate-100">{JSON.stringify(output.content, null, 2)}</pre>
      )}
    </div>
  );
}

function ainaOutputs(result?: Record<string, unknown> | null): Array<{ type: string; content: unknown }> {
  if (!result || !Array.isArray(result.outputs)) return [];
  return result.outputs.flatMap((output) => {
    if (!output || typeof output !== "object" || !("content" in output)) return [];
    const record = output as Record<string, unknown>;
    return [{ type: typeof record.type === "string" ? record.type : "output", content: record.content }];
  });
}

function ExecutionHistory({
  executions,
  loading,
}: {
  executions: ScheduledAinaExecution[];
  loading: boolean;
}) {
  const { t } = useTranslation("schedule");
  if (loading) {
    return <div className="mt-4 flex items-center justify-center gap-2 py-8 text-[12px] text-ink-muted"><Loader2 className="h-4 w-4 animate-spin" />{t("hist.loading")}</div>;
  }
  if (executions.length === 0) {
    return <div className="mt-4 rounded-lg border border-dashed border-line-strong bg-white py-8 text-center text-[12px] text-ink-muted">{t("hist.empty")}</div>;
  }
  return (
    <div className="mt-3 space-y-2">
      {executions.map((execution) => (
        <details key={execution.id} className="rounded-lg border border-line bg-white open:border-accent-ring">
          <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 px-3 py-2.5">
            <Status status={execution.status} />
            <span className={classNames("rounded-md px-1.5 py-0.5 text-[9.5px] font-bold", execution.trigger === "manual" ? "bg-accent-soft text-accent" : "bg-app-soft text-ink-muted")}>
              {execution.trigger === "manual" ? t("hist.manual") : t("hist.scheduled")}
            </span>
            <span className="text-[11.5px] font-semibold text-ink">{formatTime(execution.started_at)}</span>
            <span className="text-[10.5px] text-ink-muted">{formatDuration(execution.duration_ms)}</span>
            <span className="flex-1" />
            <span className="font-mono text-[10px] text-ink-subtle">{execution.node_id}</span>
          </summary>
          <div className="grid grid-cols-1 gap-3 border-t border-line p-3 lg:grid-cols-2">
            <TextBlock label={t("hist.input")} value={ainaInputText(execution.input)} />
            <JsonBlock label={execution.error ? t("hist.error") : t("hist.fullOutput")} value={execution.error ?? execution.result ?? {}} error={Boolean(execution.error)} />
            <div className="text-[10px] text-ink-subtle lg:col-span-2">
              {t("hist.callId")}<span className="font-mono">{execution.call_id}</span>
              {execution.scheduled_for ? <>{t("hist.plannedAt", { time: formatTime(execution.scheduled_for) })}</> : null}
            </div>
          </div>
        </details>
      ))}
    </div>
  );
}

function JsonBlock({ label, value, error = false }: { label: string; value: unknown; error?: boolean }) {
  return (
    <div>
      <p className="mb-1.5 text-[10.5px] font-bold text-ink-muted">{label}</p>
      <pre className={classNames("max-h-48 overflow-auto rounded-lg p-3 font-mono text-[10.5px] leading-relaxed", error ? "bg-danger-soft text-danger-deep" : "bg-slate-950 text-slate-100")}>
        {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

function TextBlock({ label, value }: { label: string; value: string }) {
  const { t } = useTranslation("schedule");
  return (
    <div>
      <p className="mb-1.5 text-[10.5px] font-bold text-ink-muted">{label}</p>
      <div className="min-h-24 whitespace-pre-wrap rounded-lg border border-line bg-app-soft p-3 text-[11.5px] leading-relaxed text-ink">
        {value || t("noText")}
      </div>
    </div>
  );
}

function Status({ status }: { status: ScheduledAinaStatus }) {
  const { t } = useTranslation("schedule");
  const styles: Record<ScheduledAinaStatus, string> = {
    never: "bg-app-soft text-ink-muted",
    running: "bg-warning-soft text-warning-deep",
    succeeded: "bg-success-soft text-success-deep",
    failed: "bg-danger-soft text-danger-deep",
  };
  const labels: Record<ScheduledAinaStatus, string> = { never: t("status.never"), running: t("status.running"), succeeded: t("status.succeeded"), failed: t("status.failed") };
  return <span className={classNames("rounded-md px-1.5 py-0.5 text-[9.5px] font-bold", styles[status])}>{labels[status]}</span>;
}

function formatDuration(value?: number | null): string {
  if (value == null) return i18n.t("schedule:inProgress");
  return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(2)} s`;
}

function Info({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div className="rounded-lg bg-app-soft p-2.5"><p className="text-[10px] font-bold text-ink-subtle">{label}</p><p className={classNames("mt-1 truncate text-[11.5px] text-ink", mono && "font-mono")}>{value}</p></div>;
}

function Notice({ tone, text, onClose }: { tone: "success" | "error"; text: string; onClose: () => void }) {
  const { t } = useTranslation("schedule");
  return (
    <div className={classNames("flex items-center gap-2.5 rounded-lg border p-3", tone === "success" ? "border-success/20 bg-success-soft text-success-deep" : "border-danger-ring bg-danger-soft text-danger-deep")}>
      {tone === "success" ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
      <span className="flex-1 text-[12.5px] font-semibold">{text}</span>
      <button type="button" onClick={onClose} aria-label={t("dismiss")}><X className="h-4 w-4" /></button>
    </div>
  );
}

function LoadingCards() {
  return <div className="space-y-3">{[0, 1, 2].map((item) => <div key={item} className="h-32 animate-pulse rounded-lg bg-line/60" />)}</div>;
}

function ainaInputText(input: Record<string, unknown>): string {
  for (const key of ["message", "prompt", "query", "text", "instruction"]) {
    if (typeof input[key] === "string") return input[key];
  }
  return Object.entries(input)
    .map(([key, value]) => `${key}：${typeof value === "string" ? value : JSON.stringify(value)}`)
    .join("\n");
}

function scheduleLabel(task: ScheduledAinaTask): string {
  return task.schedule_type === "cron"
    ? `${task.cron_expression} (${task.timezone})`
    : i18n.t("schedule:everySeconds", { count: task.interval_seconds });
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(currentLocale(), {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}
