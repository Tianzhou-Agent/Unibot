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
  ShieldAlert,
  Trash2,
  X,
} from "lucide-react";
import { Link } from "react-router-dom";
import { ApprovalCard, type EditedArguments } from "@/components/chat/ApprovalCard";
import { Topbar } from "@/components/layout/Topbar";
import { api, apiErrorMessage } from "@/lib/api";
import { isSystemAina } from "@/lib/plugins";
import { useMockSession } from "@/lib/mockSession";
import { classNames } from "@/lib/utils";
import type {
  AinaInstallation,
  AinaRecord,
  ApprovalRecord,
  ChatResponse,
  ScheduleType,
  ScheduledAinaExecution,
  ScheduledAinaStatus,
  ScheduledAinaTask,
} from "@/types";

interface ScheduleForm {
  executionMode: "agent" | "aina";
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
  executionMode: "agent",
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
  const { profile } = useMockSession();
  const actor = useMemo(
    () => ({ user_id: profile.actorUserId, tenant_id: profile.tenantId }),
    [profile.actorUserId, profile.tenantId],
  );
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
  const [resolvingApprovalId, setResolvingApprovalId] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "success" | "error" | "info"; text: string } | null>(null);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
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
      if (!silent) setLoading(false);
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
        (item) => item.status === "registered" && !["unibot-assistant", "unibot-scheduler"].includes(item.manifest.aina.id)
          && (isSystemAina(item.manifest.runtime) || installedIds.has(item.manifest.aina.id)),
      ),
    [ainas, installedIds],
  );
  const ainaNames = useMemo(
    () => new Map(ainas.map((item) => [item.manifest.aina.id, item.manifest.aina.name])),
    [ainas],
  );
  const enabledCount = useMemo(() => tasks.filter((task) => task.enabled).length, [tasks]);
  const pendingCount = useMemo(
    () => tasks.filter((task) => task.last_status === "approval_required").length,
    [tasks],
  );
  const nextTask = useMemo(
    () =>
      [...tasks]
        .filter((task) => task.enabled)
        .sort((left, right) => Date.parse(left.next_run_at) - Date.parse(right.next_run_at))[0] ?? null,
    [tasks],
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
      executionMode: task.aina_id ? "aina" : "agent",
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
        aina_id: form.executionMode === "aina" ? form.ainaId : "",
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
      if (form.executionMode === "aina" && !payload.aina_id) throw new Error(t("err.aina"));
      if (form.scheduleType === "interval" && (!Number.isFinite(payload.interval_seconds) || payload.interval_seconds < 10)) {
        throw new Error(t("err.interval"));
      }
      if (editingId) {
        await api.patch<ScheduledAinaTask>(`/aina-schedules/${editingId}`, payload);
      } else {
        await api.post<ScheduledAinaTask>("/aina-schedules", {
          ...payload,
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
      // A paused run shows the persistent approval banner and inline card instead of a transient notice.
      setNotice(
        updated.last_status === "approval_required"
          ? null
          : {
              tone: updated.last_status === "succeeded" ? "success" : "error",
              text: updated.last_status === "succeeded" ? t("notice.debugOk") : updated.last_error ?? t("notice.debugFail"),
            },
      );
    } catch (error) {
      setNotice({ tone: "error", text: apiErrorMessage(error) });
    } finally {
      setRunningId(null);
    }
  }

  async function resolveApproval(
    task: ScheduledAinaTask,
    approval: ApprovalRecord,
    action: "confirm" | "deny",
    edited: EditedArguments = {},
  ) {
    setResolvingApprovalId(approval.id);
    setNotice(null);
    try {
      if (action === "confirm") {
        const response = await api.post<ChatResponse>(`/approvals/${approval.id}/confirm`, {
          ...actor,
          edited_arguments: edited,
        });
        setNotice({
          tone:
            response.status === "completed"
              ? "success"
              : response.status === "approval_required"
                ? "info"
                : "error",
          text:
            response.status === "completed"
              ? t("notice.approved")
              : response.status === "approval_required"
                ? t("notice.stillPending")
                : response.content || t("notice.debugFail"),
        });
      } else {
        await api.post(`/approvals/${approval.id}/deny`, actor);
        setNotice({ tone: "info", text: t("notice.denied") });
      }
      // The backend writes the outcome back to the schedule task; reload both views.
      await Promise.all([load(true), loadExecutions(task.id)]);
    } catch (error) {
      setNotice({ tone: "error", text: apiErrorMessage(error) });
    } finally {
      setResolvingApprovalId(null);
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
      <div className={classNames("min-h-0 flex-1 overflow-y-auto", embedded ? "p-4" : "px-6 py-5")}>
        <div className={classNames("mx-auto w-full max-w-6xl", embedded ? "space-y-3" : "space-y-5")}>
          {!embedded ? (
            <section className="flex flex-wrap items-start gap-4 rounded-xl border border-line bg-white p-5 shadow-card">
              <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <CalendarClock className="h-5 w-5" />
              </div>
              <div className="min-w-0 flex-1">
                <h1 className="text-[16px] font-extrabold tracking-tight text-ink">{t("heroTitle")}</h1>
                <p className="mt-1.5 max-w-3xl text-[12.5px] leading-5 text-ink-muted">{t("heroBody")}</p>
              </div>
            </section>
          ) : null}

          {notice ? <Notice {...notice} onClose={() => setNotice(null)} /> : null}

          {pendingCount > 0 ? <PendingBanner count={pendingCount} /> : null}

          {!loading && tasks.length > 0 ? (
            <SummaryStats
              total={tasks.length}
              enabled={enabledCount}
              pending={pendingCount}
              nextRun={nextTask ? formatTime(nextTask.next_run_at) : t("summary.none")}
            />
          ) : null}

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

          {!loading && editorOpen && form.executionMode === "aina" && runnableAinas.length === 0 ? (
            <div className="rounded-lg border border-warning/30 bg-warning-soft p-3 text-[12px] text-warning-deep">
              <Trans t={t} i18nKey="noRunnable" components={{ link: <Link to="/plugin" className="font-bold underline" /> }} />
            </div>
          ) : null}

          {loading ? <LoadingCards /> : null}
          {!loading && tasks.length === 0 ? (
            <div className="flex min-h-64 flex-col items-center justify-center rounded-xl border border-dashed border-line-strong bg-white px-4 text-center">
              <Clock3 className="h-7 w-7 text-ink-subtle" />
              <h2 className="mt-2 text-[13px] font-bold text-ink">{t("emptyTitle")}</h2>
              <p className="mt-1 max-w-md text-[12px] leading-5 text-ink-muted">{t("emptyBody")}</p>
            </div>
          ) : null}
          {!loading && tasks.length > 0 ? (
            <div className="space-y-3">
              {tasks.map((task) => (
                <TaskCard
                  key={task.id}
                  task={task}
                  ainaName={task.aina_id ? ainaNames.get(task.aina_id) ?? task.aina_id : t("target.agent")}
                  detailMode={detailTaskId === task.id ? detailMode : null}
                  executions={executions[task.id] ?? []}
                  historyLoading={historyLoadingId === task.id}
                  debugPrompt={debugPrompts[task.id] ?? ""}
                  running={runningId === task.id}
                  resolvingApprovalId={resolvingApprovalId}
                  onDebugPrompt={(value) => setDebugPrompts((current) => ({ ...current, [task.id]: value }))}
                  onOpenDebug={() => void openDebug(task)}
                  onOpenHistory={() => void openHistory(task)}
                  onCloseDetail={() => setDetailTaskId(null)}
                  onRunDebug={() => void runDebug(task)}
                  onResolveApproval={(approval, action, edited) => void resolveApproval(task, approval, action, edited)}
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

function SummaryStats({
  total,
  enabled,
  pending,
  nextRun,
}: {
  total: number;
  enabled: number;
  pending: number;
  nextRun: string;
}) {
  const { t } = useTranslation("schedule");
  const items = [
    { label: t("summary.total"), value: String(total), icon: CalendarClock, tone: "text-accent" },
    { label: t("summary.enabled"), value: String(enabled), icon: CheckCircle2, tone: "text-success" },
    { label: t("summary.pending"), value: String(pending), icon: ShieldAlert, tone: pending > 0 ? "text-warning" : "text-ink-subtle" },
    { label: t("summary.nextRun"), value: nextRun, icon: Clock3, tone: "text-ink-subtle" },
  ];
  return (
    <section className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label={t("summary.aria")}>
      {items.map((item) => (
        <div key={item.label} className="flex min-h-[74px] items-center gap-3 rounded-xl border border-line bg-white px-4 py-3 shadow-card">
          <item.icon className={classNames("h-4 w-4 shrink-0", item.tone)} />
          <div className="min-w-0">
            <p className="truncate text-[10.5px] font-medium text-ink-subtle">{item.label}</p>
            <p className="mt-0.5 truncate text-[14px] font-bold text-ink">{item.value}</p>
          </div>
        </div>
      ))}
    </section>
  );
}

function PendingBanner({ count }: { count: number }) {
  const { t } = useTranslation("schedule");
  return (
    <div className="flex items-start gap-3 rounded-xl border border-warning-ring bg-warning-soft px-4 py-3 text-warning-deep">
      <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0">
        <p className="text-[12.5px] font-bold">{t("pending.title", { count })}</p>
        <p className="mt-0.5 text-[11.5px] leading-5">{t("pending.body")}</p>
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
    <section className="overflow-hidden rounded-xl border border-accent-ring bg-white shadow-card">
      <div className="flex min-h-12 items-center gap-2 border-b border-line bg-accent-soft px-4 py-2">
        <CalendarClock className="h-4 w-4 text-accent" />
        <h2 className="text-[13px] font-extrabold text-ink">{editing ? t("editor.edit") : t("editor.new")}</h2>
        <span className="flex-1" />
        <button type="button" onClick={onClose} className="btn-ghost h-8 w-8 p-0" aria-label={t("editor.close")}><X className="h-4 w-4" /></button>
      </div>
      <div className="grid grid-cols-1 gap-x-4 gap-y-3 p-4 lg:grid-cols-2">
        <label className="space-y-1.5 text-[11px] font-semibold text-ink-muted">
          {t("editor.executionMode")}
          <select value={form.executionMode} onChange={(event) => change("executionMode", event.target.value as ScheduleForm["executionMode"])} className="input-soft w-full text-[12.5px]">
            <option value="agent">{t("editor.agentMode")}</option>
            <option value="aina">{t("editor.ainaMode")}</option>
          </select>
          <span className="block text-[10px] font-normal text-ink-subtle">{t(form.executionMode === "agent" ? "editor.agentHint" : "editor.ainaHint")}</span>
        </label>
        <label className="space-y-1.5 text-[11px] font-semibold text-ink-muted">
          {t("editor.name")}
          <input value={form.name} onChange={(event) => change("name", event.target.value)} className="input-soft w-full text-[12.5px]" placeholder={t("editor.namePlaceholder")} />
        </label>
        {form.executionMode === "aina" ? (
          <label className="space-y-1.5 text-[11px] font-semibold text-ink-muted lg:col-span-2">
            {t("editor.aina")}
            <select value={form.ainaId} onChange={(event) => change("ainaId", event.target.value)} className="input-soft w-full text-[12.5px]">
              <option value="">{t("editor.ainaPlaceholder")}</option>
              {ainas.map((item) => <option key={item.manifest.aina.id} value={item.manifest.aina.id}>{item.manifest.aina.name}</option>)}
            </select>
          </label>
        ) : null}
        <label className="space-y-1.5 text-[11px] font-semibold text-ink-muted">
          {t("editor.scheduleType")}
          <select value={form.scheduleType} onChange={(event) => change("scheduleType", event.target.value as ScheduleType)} className="input-soft w-full text-[12.5px]">
            <option value="interval">{t("editor.interval")}</option>
            <option value="cron">{t("editor.cron")}</option>
          </select>
        </label>
        {form.scheduleType === "interval" ? (
          <label className="space-y-1.5 text-[11px] font-semibold text-ink-muted">
            {t("editor.intervalSeconds")}
            <input type="number" min={10} value={form.intervalSeconds} onChange={(event) => change("intervalSeconds", event.target.value)} className="input-soft w-full text-[12.5px]" />
          </label>
        ) : (
          <label className="space-y-1.5 text-[11px] font-semibold text-ink-muted">
            {t("editor.cron5")}
            <input value={form.cronExpression} onChange={(event) => change("cronExpression", event.target.value)} className="input-soft w-full font-mono text-[12.5px]" placeholder="0 9 * * 1-5" />
          </label>
        )}
        <label className="space-y-1.5 text-[11px] font-semibold text-ink-muted">
          {t("editor.timezone")}
          <input value={form.timezone} onChange={(event) => change("timezone", event.target.value)} className="input-soft w-full font-mono text-[12.5px]" placeholder="Asia/Shanghai" />
        </label>
        <label className="flex items-end gap-2 pb-2 text-[12px] font-bold text-ink-muted">
          <input type="checkbox" checked={form.enabled} onChange={(event) => change("enabled", event.target.checked)} className="h-4 w-4 accent-accent" />
          {t("editor.enableNow")}
        </label>
        <label className="space-y-1.5 text-[11px] font-semibold text-ink-muted lg:col-span-2">
          {t("editor.prompt")}
          <textarea
            value={form.prompt}
            onChange={(event) => change("prompt", event.target.value)}
            rows={5}
            placeholder={t("editor.promptPlaceholder")}
            className="input-soft resize-y bg-white text-[12.5px] leading-relaxed"
          />
          <span className="block text-[10.5px] font-normal leading-5 text-ink-subtle">{t("editor.promptHint")}</span>
        </label>
      </div>
      <div className="flex items-center justify-end gap-2 border-t border-line bg-app-soft/40 px-4 py-3">
        <button type="button" onClick={onClose} className="btn-outline h-8 text-[11.5px]">{t("editor.cancel")}</button>
        <button type="button" disabled={saving} onClick={onSave} className="btn-primary h-8 text-[11.5px]">
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
  resolvingApprovalId,
  onDebugPrompt,
  onOpenDebug,
  onOpenHistory,
  onCloseDetail,
  onRunDebug,
  onResolveApproval,
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
  resolvingApprovalId: string | null;
  onDebugPrompt: (value: string) => void;
  onOpenDebug: () => void;
  onOpenHistory: () => void;
  onCloseDetail: () => void;
  onRunDebug: () => void;
  onResolveApproval: (approval: ApprovalRecord, action: "confirm" | "deny", edited?: EditedArguments) => void;
  onEdit: () => void;
  onToggle: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation("schedule");
  const approval = pendingApproval(task.last_result);
  const resolving = resolvingApprovalId !== null && resolvingApprovalId === approval?.id;
  return (
    <article className="overflow-hidden rounded-xl border border-line bg-white shadow-card transition-colors hover:border-line-strong">
      <div className="p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent">
            <Clock3 className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="truncate text-[14px] font-extrabold text-ink">{task.name}</h2>
              <Status status={task.last_status} />
              <span
                className={classNames(
                  "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[10.5px] font-semibold",
                  task.enabled ? "bg-success-soft text-success-deep" : "bg-app-soft text-ink-muted",
                )}
              >
                <span className={classNames("h-1.5 w-1.5 rounded-full", task.enabled ? "bg-success" : "bg-ink-subtle")} />
                {task.enabled ? t("task.enabled") : t("task.paused")}
              </span>
            </div>
            <p className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[11.5px] text-ink-muted">
              <span className="font-semibold text-ink-muted">{ainaName}</span>
              <span className="text-ink-subtle"> · </span>
              <span className="font-mono text-[11px] text-ink-subtle">{scheduleLabel(task)}</span>
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-1.5 sm:justify-end">
            <button type="button" onClick={onOpenHistory} className="btn-outline h-8 px-2.5 text-[11.5px]">
              <History className="h-3.5 w-3.5" />{t("task.history")}
            </button>
            <button type="button" onClick={onOpenDebug} className="btn-primary h-8 px-2.5 text-[11.5px]">
              <Play className="h-3.5 w-3.5" />{t("task.debug")}
            </button>
            <button type="button" onClick={onEdit} className="btn-outline h-8 px-2.5 text-[11.5px]">
              <Pencil className="h-3.5 w-3.5" />{t("task.edit")}
            </button>
            <button type="button" onClick={onToggle} className="btn-outline h-8 px-2.5 text-[11.5px]">
              {task.enabled ? t("task.pause") : t("task.enable")}
            </button>
            <button
              type="button"
              onClick={onDelete}
              className="btn-danger-outline h-8 w-8 p-0"
              aria-label={t("task.deleteAria", { name: task.name })}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
          <Info label={t("task.nextRun")} value={task.enabled ? formatTime(task.next_run_at) : t("task.paused")} />
          <Info label={t("task.lastRun")} value={task.last_run_at ? formatTime(task.last_run_at) : t("task.neverRun")} />
          <Info label={t("task.node")} value={task.last_node_id ?? "—"} mono />
        </div>

        {approval ? (
          <div className="mt-4 rounded-xl border border-warning-ring bg-warning-soft/45 p-3">
            <div className="mb-2.5 flex items-start gap-2 text-warning-deep">
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <p className="text-[12px] font-bold">{t("pending.inlineTitle")}</p>
                <p className="mt-0.5 text-[11px] leading-5">{t("pending.inlineBody")}</p>
              </div>
            </div>
            <ApprovalCard
              approval={approval}
              disabled={resolving}
              debugMode
              onConfirm={(edited) => onResolveApproval(approval, "confirm", edited)}
              onDeny={() => onResolveApproval(approval, "deny")}
            />
          </div>
        ) : null}

        {task.last_error && !approval ? (
          <div className="mt-3 rounded-lg border border-danger-ring bg-danger-soft px-3 py-2.5 text-[11.5px] leading-5 text-danger-deep">
            {task.last_error}
          </div>
        ) : null}
      </div>

      {detailMode ? (
        <div className="border-t border-line bg-app-soft/60 px-4 py-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex min-w-0 items-start gap-2">
              {detailMode === "history" ? (
                <History className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
              ) : (
                <Activity className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
              )}
              <div className="min-w-0">
                <h3 className="text-[13px] font-extrabold text-ink">
                  {detailMode === "history" ? t("detail.history") : t("detail.debug", { name: ainaName })}
                </h3>
                {detailMode === "debug" ? (
                  <p className="mt-0.5 text-[11px] leading-5 text-ink-muted">{t("detail.debugHint")}</p>
                ) : null}
              </div>
            </div>
            <button type="button" onClick={onCloseDetail} className="btn-ghost h-8 w-8 p-0" aria-label={t("detail.closeAria")}>
              <X className="h-4 w-4" />
            </button>
          </div>

          {detailMode === "history" ? (
            <ExecutionHistory executions={executions} loading={historyLoading} />
          ) : (
            <>
              <div className="mt-4 rounded-xl border border-line bg-white p-3">
                <label className="block text-[11px] font-semibold text-ink-muted">
                  {t("detail.promptFor", { name: ainaName })}
                  <textarea
                    value={debugPrompt}
                    onChange={(event) => onDebugPrompt(event.target.value)}
                    rows={4}
                    aria-label={t("detail.debugAria", { name: ainaName })}
                    placeholder={t("detail.debugPlaceholder")}
                    className="input-soft mt-2 resize-y bg-white text-[12.5px] leading-relaxed"
                  />
                </label>
                <div className="mt-3 flex items-center justify-end">
                  <button type="button" disabled={running} onClick={onRunDebug} className="btn-primary h-8 text-[11.5px]">
                    {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                    {running ? t("detail.running") : t("detail.run")}
                  </button>
                </div>
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
      <div className="mt-3 flex items-center justify-center gap-2 rounded-xl border border-line bg-white py-10 text-[12px] text-ink-muted">
        <Loader2 className="h-4 w-4 animate-spin" />{t("debug.loading")}
      </div>
    );
  }
  if (!execution) {
    return (
      <div className="mt-3 rounded-xl border border-dashed border-line-strong bg-white py-10 text-center text-[12px] text-ink-muted">
        {t("debug.empty")}
      </div>
    );
  }

  const outputs = ainaOutputs(execution.result);
  const approval = pendingApproval(execution.result);
  return (
    <section className="mt-3 overflow-hidden rounded-xl border border-line bg-white">
      <header className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 border-b border-line px-4 py-3">
        <Status status={execution.status} />
        <span className="text-[12px] font-bold text-ink">{t("debug.latest")}</span>
        <span className="text-[11px] text-ink-muted">
          {formatTime(execution.started_at)} · {formatDuration(execution.duration_ms)}
        </span>
        <span className="flex-1" />
        <span className="font-mono text-[10.5px] text-ink-subtle">{execution.node_id}</span>
        <RunConversationLink execution={execution} />
      </header>
      <div className="grid grid-cols-1 items-stretch gap-4 p-4 xl:grid-cols-2">
        <TextBlock label={t("debug.input")} value={ainaInputText(execution.input)} />
        <div className="flex min-w-0 flex-col">
          <p className="mb-2 text-[11px] font-semibold text-ink-muted">{t("debug.output", { name: ainaName })}</p>
          {execution.error ? (
            <pre className="min-h-28 flex-1 overflow-auto whitespace-pre-wrap rounded-lg bg-danger-soft p-3 font-mono text-[11px] leading-relaxed text-danger-deep">{execution.error}</pre>
          ) : approval ? (
            <div className="min-h-28 flex-1 rounded-lg border border-warning-ring bg-warning-soft px-3 py-3 text-warning-deep">
              <p className="flex items-center gap-2 text-[12px] font-bold">
                <ShieldAlert className="h-4 w-4 shrink-0" />
                {t("debug.approvalPending", { names: approval.capability_names.join(t("pending.joiner")) })}
              </p>
              <p className="mt-1.5 text-[11.5px] leading-5">{t("debug.approvalHint")}</p>
            </div>
          ) : outputs.length ? (
            <div className="min-h-28 flex-1 space-y-2">
              {outputs.map((output, index) => (
                <AinaOutputValue key={`${output.type}-${index}`} output={output} />
              ))}
            </div>
          ) : (
            <pre className="min-h-28 flex-1 overflow-auto rounded-lg bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-slate-100">{JSON.stringify(execution.result ?? {}, null, 2)}</pre>
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
  if (!result) return [];
  if (typeof result.content === "string") return [{ type: "text", content: result.content }];
  if (!Array.isArray(result.outputs)) return [];
  return result.outputs.flatMap((output) => {
    if (!output || typeof output !== "object" || !("content" in output)) return [];
    const record = output as Record<string, unknown>;
    return [{ type: typeof record.type === "string" ? record.type : "output", content: record.content }];
  });
}

function pendingApproval(result?: Record<string, unknown> | null): ApprovalRecord | null {
  const value = result?.approval;
  if (!value || typeof value !== "object") return null;
  const approval = value as ApprovalRecord;
  return approval.status === "pending" ? approval : null;
}

function RunConversationLink({ execution }: { execution: ScheduledAinaExecution }) {
  const { t } = useTranslation("schedule");
  const conversationId = execution.result?.conversation_id;
  if (typeof conversationId !== "string" || !conversationId) return null;
  return (
    <Link
      to={`/chat/${encodeURIComponent(conversationId)}`}
      className="inline-flex h-7 items-center rounded-md px-2 text-[11px] font-semibold text-accent hover:bg-accent-soft"
    >
      {t(execution.status === "approval_required" ? "runConversation.approval" : "runConversation.open")}
    </Link>
  );
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
    return <div className="mt-4 flex items-center justify-center gap-2 py-10 text-[12px] text-ink-muted"><Loader2 className="h-4 w-4 animate-spin" />{t("hist.loading")}</div>;
  }
  if (executions.length === 0) {
    return <div className="mt-4 rounded-xl border border-dashed border-line-strong bg-white py-10 text-center text-[12px] text-ink-muted">{t("hist.empty")}</div>;
  }
  return (
    <div className="mt-4 space-y-2">
      {executions.map((execution) => (
        <details key={execution.id} className="group overflow-hidden rounded-xl border border-line bg-white open:border-accent-ring">
          <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 px-4 py-3 hover:bg-app-soft/50">
            <Status status={execution.status} />
            <span className={classNames("rounded-md px-2 py-0.5 text-[10.5px] font-semibold", execution.trigger === "manual" ? "bg-accent-soft text-accent" : "bg-app-soft text-ink-muted")}>
              {execution.trigger === "manual" ? t("hist.manual") : t("hist.scheduled")}
            </span>
            <span className="text-[12px] font-semibold text-ink">{formatTime(execution.started_at)}</span>
            <span className="text-[11px] text-ink-muted">{formatDuration(execution.duration_ms)}</span>
            <span className="flex-1" />
            <span className="font-mono text-[10.5px] text-ink-subtle">{execution.node_id}</span>
          </summary>
          <div className="border-t border-line px-4 pb-4 pt-2">
            <RunConversationLink execution={execution} />
            <div className="mt-2 grid grid-cols-1 items-stretch gap-4 lg:grid-cols-2">
              <TextBlock label={t("hist.input")} value={ainaInputText(execution.input)} />
              {!execution.error && typeof execution.result?.content === "string"
                ? <TextBlock label={t("hist.fullOutput")} value={execution.result.content} />
                : <JsonBlock label={execution.error ? t("hist.error") : t("hist.fullOutput")} value={execution.error ?? execution.result ?? {}} error={Boolean(execution.error)} />}
            </div>
            <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-[10.5px] text-ink-subtle">
              <span>{t("hist.callId")}<span className="font-mono">{execution.call_id}</span></span>
              {execution.scheduled_for ? <span>{t("hist.plannedAt", { time: formatTime(execution.scheduled_for) })}</span> : null}
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
    <div className="flex min-w-0 flex-col">
      <p className="mb-2 text-[11px] font-semibold text-ink-muted">{label}</p>
      <div className="min-h-28 flex-1 whitespace-pre-wrap rounded-lg border border-line bg-app-soft p-3 text-[12px] leading-6 text-ink">
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
    approval_required: "bg-warning-soft text-warning-deep",
    succeeded: "bg-success-soft text-success-deep",
    failed: "bg-danger-soft text-danger-deep",
  };
  const dots: Record<ScheduledAinaStatus, string> = {
    never: "bg-ink-subtle",
    running: "bg-warning animate-pulse",
    approval_required: "bg-warning",
    succeeded: "bg-success",
    failed: "bg-danger",
  };
  const labels: Record<ScheduledAinaStatus, string> = {
    never: t("status.never"),
    running: t("status.running"),
    approval_required: t("status.approval_required"),
    succeeded: t("status.succeeded"),
    failed: t("status.failed"),
  };
  return (
    <span className={classNames("inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[10.5px] font-semibold", styles[status])}>
      <span className={classNames("h-1.5 w-1.5 rounded-full", dots[status])} />
      {labels[status]}
    </span>
  );
}

function formatDuration(value?: number | null): string {
  if (value == null) return i18n.t("schedule:inProgress");
  return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(2)} s`;
}

function Info({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="rounded-lg border border-line bg-app-soft/60 px-3 py-2.5">
      <p className="text-[10.5px] font-medium text-ink-subtle">{label}</p>
      <p className={classNames("mt-1 truncate text-[12px] font-semibold text-ink", mono && "font-mono")}>{value}</p>
    </div>
  );
}

function Notice({ tone, text, onClose }: { tone: "success" | "error" | "info"; text: string; onClose: () => void }) {
  const { t } = useTranslation("schedule");
  return (
    <div className={classNames("flex items-center gap-2.5 rounded-xl border px-4 py-3", tone === "success" ? "border-success/20 bg-success-soft text-success-deep" : tone === "info" ? "border-accent-ring bg-accent-soft text-accent" : "border-danger-ring bg-danger-soft text-danger-deep")}>
      {tone === "success" ? <CheckCircle2 className="h-4 w-4 shrink-0" /> : <AlertCircle className="h-4 w-4 shrink-0" />}
      <span className="flex-1 text-[12px] font-semibold leading-5">{text}</span>
      <button type="button" onClick={onClose} aria-label={t("dismiss")} className="rounded p-0.5 hover:bg-black/5"><X className="h-4 w-4" /></button>
    </div>
  );
}

function LoadingCards() {
  return <div className="space-y-3">{[0, 1, 2].map((item) => <div key={item} className="h-36 animate-pulse rounded-xl bg-line/60" />)}</div>;
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
