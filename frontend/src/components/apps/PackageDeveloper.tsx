import { useTranslation } from "react-i18next";
import { type FormEvent, useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, Download, Loader2, PackageCheck, Sparkles, X } from "lucide-react";
import { api, apiErrorMessage } from "@/lib/api";
import { classNames, downloadBlob } from "@/lib/utils";
import type { AinaRecord, PackageValidationReport } from "@/types";

const DEFAULT_SCAFFOLD = { aina_id: "", name: "", description: "", mcp_url: "http://127.0.0.1:8770/mcp" };

/** Download a new AINA 2.0 project (package + MCP server), the same files `aina init` writes. */
export function PackageScaffoldForm({ onDone, onCancel }: { onDone: (message: string) => void; onCancel: () => void }) {
  const { t } = useTranslation("apps");
  const [value, setValue] = useState(DEFAULT_SCAFFOLD);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.postBlob("/ainas/packages/scaffold", value);
      downloadBlob(result.blob, result.filename ?? `${value.aina_id}.zip`);
      onDone(t("package.templateDownloaded"));
    } catch (scaffoldError) {
      setError(apiErrorMessage(scaffoldError));
    } finally {
      setBusy(false);
    }
  }

  const field = (
    key: keyof typeof DEFAULT_SCAFFOLD,
    label: string,
    placeholder: string,
    { wide = false, pattern, hint, invalid }: { wide?: boolean; pattern?: string; hint?: string; invalid?: string } = {},
  ) => (
    <label className={classNames("block", wide && "sm:col-span-2")}>
      <span className="mb-1.5 block text-[11px] font-bold text-ink-muted">{label}</span>
      <input
        required
        pattern={pattern}
        value={value[key]}
        onChange={(event) => {
          event.currentTarget.setCustomValidity("");
          setValue({ ...value, [key]: event.target.value });
        }}
        onInvalid={(event) => {
          if (invalid && event.currentTarget.validity.patternMismatch) event.currentTarget.setCustomValidity(invalid);
        }}
        className="input-soft"
        placeholder={placeholder}
      />
      {hint ? <span className="mt-1 block text-[10.5px] text-ink-muted">{hint}</span> : null}
    </label>
  );

  return (
    <form onSubmit={(event) => void submit(event)} className="rounded-2xl border border-line bg-white p-4" aria-label={t("package.newTitle")}>
      <div className="flex items-center gap-3">
        <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-soft text-accent"><Sparkles className="h-5 w-5" /></span>
        <div className="min-w-0">
          <h2 className="text-[14px] font-semibold text-ink">{t("package.newTitle")}</h2>
          <p className="text-[11.5px] text-ink-muted">{t("package.newDesc")}</p>
        </div>
      </div>
      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {field("aina_id", t("package.id"), "com.example.notes", {
          // aina.schema.json: a reverse-domain id, lowercase.
          pattern: "[a-z0-9]+(\\.[a-z0-9\\-]+)+",
          hint: t("package.idHint"),
          invalid: t("package.idInvalid"),
        })}
        {field("name", t("package.name"), "Notes")}
        {field("description", t("package.description"), t("package.descriptionPlaceholder"), { wide: true })}
        {field("mcp_url", t("package.mcpUrl"), "http://127.0.0.1:8770/mcp", { wide: true })}
      </div>
      <p className="mt-3 text-[11px] leading-5 text-ink-muted">{t("package.cliHint")} <code className="font-mono">aina init</code></p>
      {error ? <p className="mt-2 text-[11.5px] text-danger" role="alert">{error}</p> : null}
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="btn-outline rounded-full">{t("scaffold.cancel")}</button>
        <button type="submit" disabled={busy} className="btn rounded-full bg-ink text-white hover:bg-black">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          {t("package.download")}
        </button>
      </div>
    </form>
  );
}

/** What an uploaded package contains and whether it passed the host's checks; admins register it from here. */
export function PackageReviewDialog({
  file,
  report,
  canRegister,
  onClose,
  onRegistered,
}: {
  file: File;
  report: PackageValidationReport;
  canRegister: boolean;
  onClose: () => void;
  onRegistered: (record: AinaRecord) => void;
}) {
  const { t } = useTranslation("apps");
  const [credential, setCredential] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const manifest = report.manifest;
  const valid = Boolean(manifest) && report.errors.length === 0;
  const needsCredential = manifest ? ["bearer", "api_key"].includes(manifest.tools.mcp.auth.type) : false;

  async function register() {
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      if (credential.trim()) form.append("credential", credential.trim());
      onRegistered(await api.postForm<AinaRecord>("/ainas/packages", form));
    } catch (registerError) {
      setError(apiErrorMessage(registerError));
      setBusy(false);
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/45 p-4" role="presentation">
      <div role="dialog" aria-modal="true" aria-label={t("package.reviewTitle")} className="flex max-h-[90vh] w-full max-w-lg flex-col rounded-xl border border-line bg-white text-ink shadow-xl">
        <div className="flex items-start gap-3 border-b border-line p-5">
          <span className={classNames("flex h-10 w-10 shrink-0 items-center justify-center rounded-xl", valid ? "bg-success-soft text-success" : "bg-danger-soft text-danger")}>
            {valid ? <PackageCheck className="h-5 w-5" /> : <AlertTriangle className="h-5 w-5" />}
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="text-[15px] font-extrabold">{manifest ? `${manifest.aina.name} ${manifest.aina.version}` : file.name}</h2>
            <p className="mt-1 text-[11.5px] text-ink-muted">{valid ? t("package.valid") : t("package.invalid", { count: report.errors.length })}</p>
          </div>
          <button type="button" onClick={onClose} disabled={busy} className="flex h-8 w-8 items-center justify-center rounded-lg text-ink-muted hover:bg-app-soft hover:text-ink" aria-label={t("package.close")}><X className="h-4 w-4" /></button>
        </div>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5 text-[11.5px]">
          {report.errors.length ? (
            <section>
              <h3 className="font-bold text-danger">{t("package.errors")}</h3>
              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-danger-deep">{report.errors.map((item) => <li key={item}>{item}</li>)}</ul>
            </section>
          ) : null}
          {report.warnings.length ? (
            <section>
              <h3 className="font-bold text-warning-deep">{t("package.warnings")}</h3>
              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-warning-deep">{report.warnings.map((item) => <li key={item}>{item}</li>)}</ul>
            </section>
          ) : null}
          {manifest ? (
            <>
              <section>
                <h3 className="font-bold">{t("package.server")}</h3>
                <p className="mt-1 break-all font-mono text-[11px]">{manifest.tools.mcp.url}</p>
              </section>
              <section>
                <h3 className="font-bold">{t("package.tools")}</h3>
                <ul className="mt-1 space-y-0.5">
                  {Object.entries(manifest.tools.expose ?? {}).map(([name, tool]) => (
                    <li key={name}><span className="font-mono">{name}</span> <span className="text-ink-muted">· {tool.effect} · {tool.visibility.join(", ")}</span></li>
                  ))}
                </ul>
              </section>
              {report.skills.length ? (
                <section>
                  <h3 className="font-bold">{t("package.skills")}</h3>
                  <ul className="mt-1 space-y-0.5">{report.skills.map((skill) => <li key={skill.id}><span className="font-semibold">{skill.name}</span> <span className="text-ink-muted">· {skill.description}</span></li>)}</ul>
                </section>
              ) : null}
              <section>
                <h3 className="font-bold">{t("package.permissions")}</h3>
                <p className="mt-1">{manifest.permissions.length ? manifest.permissions.map((item) => item.scope + (item.optional ? ` (${t("package.optional")})` : "")).join(", ") : t("package.none")}</p>
              </section>
              {manifest.canvas ? <p><span className="font-bold">{t("package.canvas")}:</span> {manifest.canvas.title}</p> : null}
            </>
          ) : null}
          {valid && canRegister && needsCredential ? (
            <label className="block">
              <span className="mb-1 block font-bold">{t("package.credential")}</span>
              <input type="password" value={credential} onChange={(event) => setCredential(event.target.value)} className="input-soft" autoComplete="off" />
              <span className="mt-1 block text-ink-muted">{t("package.credentialHint")}</span>
            </label>
          ) : null}
          {valid && !canRegister ? <p className="text-ink-muted">{t("package.adminOnly")}</p> : null}
          {error ? <p className="text-danger" role="alert">{error}</p> : null}
        </div>
        <div className="flex justify-end gap-2 border-t border-line p-4">
          <button type="button" onClick={onClose} disabled={busy} className="btn-outline rounded-full">{t("package.close")}</button>
          {canRegister ? (
            <button type="button" onClick={() => void register()} disabled={!valid || busy} className="btn rounded-full bg-ink text-white hover:bg-black disabled:opacity-50">
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <PackageCheck className="h-4 w-4" />}
              {t("package.register")}
            </button>
          ) : null}
        </div>
      </div>
    </div>,
    document.body,
  );
}
