import { useTranslation } from "react-i18next";
import { type FormEvent, useState } from "react";
import { Download, Loader2, Sparkles } from "lucide-react";
import { api, apiErrorMessage } from "@/lib/api";
import { classNames, downloadBlob } from "@/lib/utils";

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
