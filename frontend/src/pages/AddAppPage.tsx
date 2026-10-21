import { useTranslation } from "react-i18next";
import { useEffect, useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AlertTriangle, ChevronRight, Download, Loader2, Package, PackageCheck, ShieldCheck } from "lucide-react";
import { Topbar } from "@/components/layout/Topbar";
import { CanvasPart, McpServersPart, SkillsPart } from "@/components/apps/AddAppParts";
import { PackageScaffoldForm } from "@/components/apps/PackageDeveloper";
import { api, ApiError } from "@/lib/api";
import {
  ID_PATTERN,
  MAX_DESCRIPTION,
  composeForm,
  draftFromManifest,
  draftProblems,
  duplicateTools,
  emptyDraft,
  nextVersion,
  releaseVersion,
  suggestedId,
  type AppDraft,
} from "@/lib/addApp";
import { useAuth } from "@/lib/auth";
import { useNotify } from "@/lib/notify";
import { classNames, downloadBlob } from "@/lib/utils";
import type { AinaRecord, PackageValidationReport } from "@/types";

interface ComposeCheck {
  report: PackageValidationReport;
  canvas_preview: string | null;
}

/**
 * Add an app: upload a finished package, or build one from an MCP server, skills and a canvas. With an ``ainaId``
 * (``/plugin/:ainaId/edit``) it edits a registered package, starting from its archive, as its next version.
 */
export default function AddAppPage() {
  const { t } = useTranslation("addApp");
  const { ainaId } = useParams();
  const editing = Boolean(ainaId);
  const navigate = useNavigate();
  const { user, config } = useAuth();
  const canRegister = !config.auth_required || Boolean(user?.is_admin);
  const [draft, setDraft] = useState<AppDraft>(emptyDraft);
  const [base, setBase] = useState<{ file: File; manifest: Record<string, unknown> } | null>(null);
  // When editing, the version already registered; any package loaded here is saved past it.
  const [registeredVersion, setRegisteredVersion] = useState<string | null>(null);
  const [check, setCheck] = useState<ComposeCheck | null>(null);
  const [busy, setBusy] = useState<"upload" | "check" | "download" | "register" | null>(null);
  // Set when registering found this version already taken; a new object each time, so the field is shown again.
  const [versionTaken, setVersionTaken] = useState<{ version: string } | null>(null);
  const [showProblems, setShowProblems] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [starter, setStarter] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const packageInput = useRef<HTMLInputElement>(null);
  const versionInput = useRef<HTMLInputElement>(null);
  const notify = useNotify();

  const problems = draftProblems(draft);
  const duplicates = duplicateTools(draft);
  const flagged = (problem: string) => showProblems && problems.includes(problem);
  const change = (update: Partial<AppDraft>) => {
    setDraft((current) => ({ ...current, ...update }));
    setCheck(null);
  };

  useEffect(() => {
    if (!ainaId) return;
    setBusy("upload");
    api
      .getBlob(`/ainas/${encodeURIComponent(ainaId)}/package`)
      .then((result) => usePackage(new File([result.blob], result.filename ?? `${ainaId}.aina.zip`), true))
      .catch((loadError) => {
        notify.error(t("failed.load"), loadError);
        setBusy(null);
      });
  }, [ainaId]);

  useEffect(() => {
    if (!versionTaken) return;
    versionInput.current?.scrollIntoView({ block: "center", behavior: "smooth" });
    versionInput.current?.focus({ preventScroll: true });
  }, [versionTaken]);

  async function usePackage(file: File | undefined, isRegistered = false) {
    if (!file) return;
    setBusy("upload");
    try {
      const form = new FormData();
      form.append("file", file);
      const report = await api.postForm<PackageValidationReport>("/ainas/packages/validate", form);
      if (!report.manifest) {
        setCheck({ report, canvas_preview: null });
        return;
      }
      const manifest = report.manifest as unknown as Record<string, unknown>;
      setBase({ file, manifest });
      const loaded = draftFromManifest(manifest, report.skills);
      const taken = isRegistered ? loaded.version : registeredVersion;
      if (isRegistered) setRegisteredVersion(loaded.version);
      setDraft(taken ? { ...loaded, version: releaseVersion(loaded.version, taken) } : loaded);
      setCheck(null);
    } catch (uploadError) {
      notify.error(t("failed.upload"), uploadError);
    } finally {
      setBusy(null);
    }
  }

  function ready(): boolean {
    setShowProblems(true);
    return problems.length === 0;
  }

  async function run(mode: "check" | "download" | "register") {
    if (!ready()) return;
    setBusy(mode);
    try {
      const form = composeForm(draft, base);
      if (mode === "download") {
        const result = await api.postFormBlob("/ainas/packages/compose?mode=download", form);
        downloadBlob(result.blob, result.filename ?? `${draft.id}.aina.zip`);
      } else if (mode === "check") {
        setCheck(await api.postForm<ComposeCheck>("/ainas/packages/compose?mode=check", form));
      } else {
        const record = await api.postForm<AinaRecord>("/ainas/packages/compose?mode=register", form);
        navigate(`/plugin/${encodeURIComponent(record.manifest.aina.id)}`);
      }
    } catch (runError) {
      if (mode === "register" && runError instanceof ApiError && errorCode(runError) === "VERSION_EXISTS") {
        setVersionTaken({ version: draft.version });
      } else {
        notify.error(t(mode !== "register" ? `failed.${mode}` : editing ? "failed.save" : "failed.register"), runError);
      }
    } finally {
      setBusy(null);
    }
  }

  function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    void usePackage(event.dataTransfer.files[0]);
  }

  const report = check?.report;
  const versionError = versionTaken && versionTaken.version === draft.version
    ? nextVersion(draft.version) !== draft.version
      ? t("problems.versionTakenNext", { version: draft.version, next: nextVersion(draft.version) })
      : t("problems.versionTaken", { version: draft.version })
    : undefined;
  const title = editing ? t("edit.title") : t("title");
  return (
    <div className="flex h-full flex-col bg-app-bg">
      <Topbar title={title} />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-5 py-7 md:px-8 md:py-9">
          <nav className="flex items-center gap-1 text-[12.5px] text-ink-muted" aria-label={t("breadcrumb")}>
            <Link to="/plugin" className="hover:text-ink">{t("plugins")}</Link>
            <ChevronRight className="h-3.5 w-3.5" />
            {ainaId ? (
              <>
                <Link to={`/plugin/${encodeURIComponent(ainaId)}`} className="hover:text-ink">{draft.name || ainaId}</Link>
                <ChevronRight className="h-3.5 w-3.5" />
              </>
            ) : null}
            <span className="text-ink">{title}</span>
          </nav>
          <header className="mt-2 flex flex-wrap items-baseline justify-between gap-3">
            <div>
              <h2 className="text-[26px] font-semibold text-ink">{title}</h2>
              <p className="mt-1 text-[14px] text-ink-muted">{editing ? t("edit.subtitle") : t("subtitle")}</p>
            </div>
            {!editing ? (
              <button type="button" onClick={() => setStarter((open) => !open)} className="text-[13px] text-accent hover:underline">
                {t("starter.link")}
              </button>
            ) : null}
          </header>
          {starter ? (
            <div className="mt-4">
              <PackageScaffoldForm onDone={(message) => { setStarter(false); setNotice(message); }} onCancel={() => setStarter(false)} />
            </div>
          ) : null}
          {notice ? <p role="status" className="mt-4 rounded-lg bg-success-soft px-3 py-2 text-[12.5px] text-success">{notice}</p> : null}

          <div
            onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={drop}
            className={classNames("mt-6 flex items-center gap-3 rounded-xl border border-dashed p-4", dragging ? "border-accent bg-accent-soft" : "border-line-strong bg-white")}
          >
            <Package className="h-6 w-6 shrink-0 text-ink-muted" />
            <div className="min-w-0 flex-1">
              <p className="text-[14px] font-semibold text-ink">{base ? t("package.loaded", { name: base.file.name }) : t("package.title")}</p>
              <p className="text-[12.5px] text-ink-muted">{t("package.hint")}</p>
            </div>
            <button type="button" onClick={() => packageInput.current?.click()} className="btn-outline rounded-lg" disabled={busy === "upload"}>
              {busy === "upload" ? <Loader2 className="h-4 w-4 animate-spin" /> : null}{t("package.choose")}
            </button>
            <input ref={packageInput} type="file" accept=".zip,application/zip" hidden aria-label={t("package.choose")} onChange={(event: ChangeEvent<HTMLInputElement>) => { void usePackage(event.target.files?.[0]); event.target.value = ""; }} />
          </div>

          <section className="mt-8" aria-labelledby="about-title">
            <h3 id="about-title" className="text-[16px] font-semibold text-ink">{t("about.title")}</h3>
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label={t("about.name")} required error={flagged("name") ? t("problems.name") : undefined}>
                <input
                  value={draft.name}
                  onChange={(event) => change({ name: event.target.value, ...(draft.idEdited ? {} : { id: suggestedId(event.target.value) }) })}
                  placeholder={t("about.namePlaceholder")}
                  className="input-soft"
                />
              </Field>
              <Field label={t("about.id")} hint={t("about.idHint")} error={flagged("id") ? t("problems.id") : undefined}>
                <input
                  value={draft.id}
                  disabled={editing}
                  onChange={(event) => change({ id: event.target.value.trim().toLowerCase(), idEdited: true })}
                  placeholder="com.example.notes"
                  className={classNames("input-soft font-mono", draft.id && !ID_PATTERN.test(draft.id) && "border-danger")}
                />
              </Field>
            </div>
            <Field
              className="mt-3"
              label={t("about.description")}
              required
              error={flagged("description") ? t("problems.description") : undefined}
              hint={t("about.descriptionHint")}
              counter={`${draft.description.length} / ${MAX_DESCRIPTION}`}
            >
              <textarea
                value={draft.description}
                maxLength={MAX_DESCRIPTION}
                onChange={(event) => change({ description: event.target.value })}
                placeholder={t("about.descriptionPlaceholder")}
                rows={3}
                className="input-soft resize-y"
              />
            </Field>
            {/[^\x00-\x7F]/.test(draft.name + draft.description) ? <p className="mt-1.5 text-[11.5px] text-warning-deep">{t("about.english")}</p> : null}
            <details open={editing || Boolean(versionError)} className="mt-3 text-[12.5px] text-ink-muted">
              <summary className="cursor-pointer">{t("about.more")}</summary>
              <Field className="mt-2 max-w-md" label={t("about.version")} hint={editing ? t("about.versionHint") : undefined} error={versionError}>
                <input
                  ref={versionInput}
                  value={draft.version}
                  onChange={(event) => change({ version: event.target.value.trim() })}
                  aria-invalid={Boolean(versionError)}
                  className={classNames("input-soft w-40 font-mono", versionError && "border-danger")}
                />
              </Field>
            </details>
          </section>

          <section className="mt-8" aria-labelledby="parts-title">
            <h3 id="parts-title" className="text-[16px] font-semibold text-ink">{t("parts.title")}</h3>
            <p className={classNames("mt-0.5 text-[12.5px]", flagged("parts") ? "text-danger" : "text-ink-muted")}>{t("parts.hint")}</p>
            <div className="mt-3 space-y-3">
              <McpServersPart servers={draft.servers} onChange={(servers) => change({ servers })} invalid={flagged("tools") || duplicates.length > 0} />
              {flagged("tools") ? <p className="text-[12px] text-danger">{t("problems.tools")}</p> : null}
              {duplicates.length ? <p role="alert" className="text-[12px] text-danger">{t("problems.duplicate", { names: duplicates.join(", ") })}</p> : null}
              <SkillsPart skills={draft.skills} onChange={(skills) => change({ skills })} />
              <CanvasPart
                canvas={draft.canvas}
                fromPackage={Boolean(base && (base.manifest.canvas as { entry?: string } | undefined)?.entry)}
                preview={check?.canvas_preview ?? null}
                onChange={(canvas) => change({ canvas })}
              />
            </div>
          </section>

          {report ? <CheckResult report={report} /> : null}

          <footer className="sticky bottom-0 mt-6 flex flex-wrap items-center justify-end gap-2 border-t border-line bg-app-bg py-4">
            {!canRegister ? <span className="mr-auto text-[12px] text-ink-muted">{t("adminOnly")}</span> : null}
            <button type="button" onClick={() => void run("download")} disabled={busy !== null} className="btn-outline rounded-full">
              {busy === "download" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}{t("actions.download")}
            </button>
            <button type="button" onClick={() => void run("check")} disabled={busy !== null} className="btn-outline rounded-full">
              {busy === "check" ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}{t("actions.check")}
            </button>
            {canRegister ? (
              <button type="button" onClick={() => void run("register")} disabled={busy !== null} className="btn rounded-full bg-ink text-white hover:bg-black">
                {busy === "register" ? <Loader2 className="h-4 w-4 animate-spin" /> : <PackageCheck className="h-4 w-4" />}{editing ? t("actions.save") : t("actions.register")}
              </button>
            ) : null}
          </footer>
        </div>
      </div>
    </div>
  );
}

function errorCode(error: ApiError): string | undefined {
  return (error.body as { error?: { code?: string } } | null)?.error?.code;
}

function Field({
  label,
  required,
  hint,
  error,
  counter,
  className,
  children,
}: {
  label: string;
  required?: boolean;
  hint?: string;
  error?: string;
  counter?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <label className={classNames("block", className)}>
      <span className="mb-1.5 block text-[12px] font-semibold text-ink-muted">
        {label}{required ? <span className="ml-0.5 text-danger" aria-hidden="true">*</span> : null}
      </span>
      {children}
      {error ? <span role="alert" className="mt-1 block text-[11.5px] text-danger">{error}</span> : null}
      {hint || counter ? (
        <span className="mt-1 flex justify-between gap-3 text-[11px] text-ink-muted">
          <span>{hint}</span>
          {counter ? <span className="shrink-0">{counter}</span> : null}
        </span>
      ) : null}
    </label>
  );
}

function CheckResult({ report }: { report: PackageValidationReport }) {
  const { t } = useTranslation("addApp");
  if (!report.errors.length && !report.warnings.length) {
    return <p role="status" className="mt-6 flex items-center gap-2 rounded-lg bg-success-soft px-3 py-2 text-[12.5px] text-success"><ShieldCheck className="h-4 w-4" />{t("check.passed")}</p>;
  }
  return (
    <div role="status" className="mt-6 space-y-2 text-[12.5px]">
      {report.errors.length ? (
        <div className="rounded-lg bg-danger-soft px-3 py-2 text-danger-deep">
          <p className="flex items-center gap-1.5 font-semibold"><AlertTriangle className="h-4 w-4" />{t("check.errors", { count: report.errors.length })}</p>
          <ul className="mt-1 list-disc pl-5">{report.errors.map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
      ) : null}
      {report.warnings.length ? (
        <div className="rounded-lg bg-warning-soft px-3 py-2 text-warning-deep">
          <p className="font-semibold">{t("check.warnings", { count: report.warnings.length })}</p>
          <ul className="mt-1 list-disc pl-5">{report.warnings.map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
      ) : null}
    </div>
  );
}
