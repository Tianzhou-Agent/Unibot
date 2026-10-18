import { useMemo, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { ShieldCheck, X } from "lucide-react";
import { apiErrorMessage } from "@/lib/api";
import { installPackage } from "@/lib/plugins";
import { classNames } from "@/lib/utils";
import type { AinaConfigField, AinaInstallation, AinaRecord } from "@/types";

// Design §10: which scopes are granted without asking, and how each is highlighted.
const AUTO_GRANTED = new Set(["storage", "notify"]);
const HIGH_RISK = new Set(["documents.write", "memory.write", "sandbox.exec"]);

type ConfigValue = string | boolean;

/** Install consent for an AINA 2.0 package: who runs it, what it can do, which data it reaches, its settings. */
export function InstallConsentDialog({
  record,
  installation,
  onClose,
  onInstalled,
}: {
  record: AinaRecord;
  installation?: AinaInstallation | null;
  onClose: () => void;
  onInstalled: () => void;
}) {
  const { t } = useTranslation("consent");
  const runtime = record.manifest.runtime;
  const pkg = runtime.type === "package" ? runtime.package : null;
  const fields = Object.entries(pkg?.config?.schema.properties ?? {});
  const requiredFields = new Set(pkg?.config?.schema.required ?? []);
  const [optional, setOptional] = useState<Set<string>>(
    () => new Set(installation?.granted_permissions ?? []),
  );
  const [values, setValues] = useState<Record<string, ConfigValue>>(() =>
    Object.fromEntries(
      fields
        .filter(([, field]) => !field["x-secret"])
        .map(([name, field]) => [name, initialValue(field, installation?.configuration[name])]),
    ),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const origin = useMemo(() => (pkg ? safeOrigin(pkg.tools.mcp.url) : ""), [pkg]);

  if (!pkg || runtime.type !== "package") return null;
  const tools = runtime.tools;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!pkg) return;
    setSaving(true);
    setError(null);
    const configuration: Record<string, unknown> = {};
    for (const [name, field] of fields) {
      const value = values[name];
      if (value === undefined || value === "") continue; // an empty secret keeps the saved one
      configuration[name] = field.type === "number" || field.type === "integer" ? Number(value) : value;
    }
    const granted = pkg.permissions
      .filter((item) => !item.optional || optional.has(item.scope))
      .map((item) => item.scope);
    try {
      await installPackage(record, granted, configuration);
      onInstalled();
    } catch (installError) {
      setError(apiErrorMessage(installError));
      setSaving(false);
    }
  }

  return createPortal(
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/45 p-4"
      role="presentation"
      onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) onClose(); }}
    >
      <form
        onSubmit={(event) => void submit(event)}
        role="dialog"
        aria-modal="true"
        aria-label={t("title", { name: record.manifest.aina.name })}
        className="flex max-h-[90vh] w-full max-w-lg flex-col rounded-xl border border-line bg-white text-ink shadow-xl"
      >
        <div className="flex items-start gap-3 border-b border-line p-5">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent"><ShieldCheck className="h-5 w-5" /></span>
          <div className="min-w-0 flex-1">
            <h2 className="text-[15px] font-extrabold">{t("title", { name: record.manifest.aina.name })}</h2>
            <p className="mt-1 text-[11.5px] text-ink-muted">
              {t("subtitle", { publisher: pkg.aina.publisher.name, version: pkg.aina.version })}
            </p>
          </div>
          <button type="button" onClick={onClose} disabled={saving} className="flex h-8 w-8 items-center justify-center rounded-lg text-ink-muted hover:bg-app-soft hover:text-ink" aria-label={t("close")}><X className="h-4 w-4" /></button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5 text-[11.5px]">
          <section>
            <h3 className="font-bold">{t("server")}</h3>
            <p className="mt-1 font-mono text-[11px]">{origin}</p>
            <p className="mt-0.5 text-ink-muted">{t("serverHint")}</p>
            {pkg.canvas?.url ? (
              <p className="mt-1.5"><span className="font-bold">{t("canvas")}:</span> <span className="font-mono text-[11px]">{safeOrigin(pkg.canvas.url)}</span></p>
            ) : null}
          </section>

          <section>
            <h3 className="font-bold">{t("tools")}</h3>
            <ul className="mt-1.5 space-y-1">
              {tools.map((tool) => (
                <li key={tool.name} className="flex items-start gap-2">
                  <span className={classNames(
                    "mt-px shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold",
                    tool.effect === "destructive" ? "bg-danger-soft text-danger" : tool.effect === "write" ? "bg-accent-soft text-accent" : "bg-app-soft text-ink-muted",
                  )}>{t(`effect.${tool.effect}`)}</span>
                  <span className="min-w-0">
                    <span className="font-semibold">{tool.title || tool.name}</span>
                    {!tool.visibility.includes("model") ? <span className="text-ink-subtle"> · {t("appOnly")}</span> : null}
                    {tool.status !== "active" ? <span className="text-danger"> · {t("disabled")}</span> : null}
                    <span className="block text-ink-muted">{tool.description}</span>
                  </span>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h3 className="font-bold">{t("permissions")}</h3>
            {pkg.permissions.length === 0 ? <p className="mt-1 text-ink-muted">{t("noPermissions")}</p> : (
              <ul className="mt-1.5 space-y-1.5">
                {pkg.permissions.map((item) => {
                  const auto = AUTO_GRANTED.has(item.scope);
                  const high = HIGH_RISK.has(item.scope);
                  const chosen = auto || !item.optional || optional.has(item.scope);
                  return (
                    <li key={item.scope}>
                      <label className={classNames("flex items-start gap-2 rounded-lg border px-2.5 py-2", high ? "border-danger-ring bg-danger-soft" : "border-line")}>
                        <input
                          type="checkbox"
                          className="mt-0.5"
                          checked={chosen}
                          disabled={auto || !item.optional || saving}
                          onChange={(event) => setOptional((current) => {
                            const next = new Set(current);
                            if (event.target.checked) next.add(item.scope);
                            else next.delete(item.scope);
                            return next;
                          })}
                        />
                        <span className="min-w-0">
                          <span className="font-semibold">{t(`scope.${item.scope.replace(".", "_")}`, { defaultValue: item.scope })}</span>
                          <span className="text-ink-subtle"> · {auto ? t("auto") : item.optional ? t("optionalHint") : t("required")} · {t(`risk.${high ? "high" : auto ? "low" : "medium"}`)}</span>
                          {item.reason ? <span className="block text-ink-muted">{item.reason}</span> : null}
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          {fields.length ? (
            <section>
              <h3 className="font-bold">{t("settings")}</h3>
              <div className="mt-1.5 space-y-2.5">
                {fields.map(([name, field]) => (
                  <ConfigInput
                    key={name}
                    name={name}
                    field={field}
                    required={requiredFields.has(name) && !installation?.configured_secrets?.includes(name)}
                    saved={Boolean(field["x-secret"] && installation?.configured_secrets?.includes(name))}
                    value={values[name]}
                    disabled={saving}
                    onChange={(value) => setValues((current) => ({ ...current, [name]: value }))}
                  />
                ))}
              </div>
            </section>
          ) : null}

        </div>

        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line p-4">
          {error ? <p role="alert" className="w-full rounded-lg border border-danger-ring bg-danger-soft px-3 py-2 text-[11.5px] text-danger-deep">{error}</p> : null}
          <button type="button" onClick={onClose} disabled={saving} className="btn-outline">{t("cancel")}</button>
          <button type="submit" disabled={saving} className="btn-primary disabled:opacity-50">{saving ? t("installing") : t("install")}</button>
        </div>
      </form>
    </div>,
    document.body,
  );
}

function ConfigInput({
  name,
  field,
  required,
  saved,
  value,
  disabled,
  onChange,
}: {
  name: string;
  field: AinaConfigField;
  required: boolean;
  saved: boolean;
  value: ConfigValue | undefined;
  disabled: boolean;
  onChange: (value: ConfigValue) => void;
}) {
  const { t } = useTranslation("consent");
  const label = field.title || name;
  if (field.type === "boolean") {
    return (
      <label className="flex items-center gap-2 font-semibold">
        <input type="checkbox" checked={value === true} disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
        {label}
      </label>
    );
  }
  const secret = field["x-secret"] === true;
  return (
    <label className="block font-semibold">
      {label}{required ? " *" : ""}
      {field.enum ? (
        <select value={String(value ?? "")} required={required} disabled={disabled} onChange={(event) => onChange(event.target.value)} className="input-soft mt-1">
          {!required ? <option value="" /> : null}
          {field.enum.map((option) => <option key={String(option)} value={String(option)}>{String(option)}</option>)}
        </select>
      ) : (
        <input
          type={secret ? "password" : field.type === "number" || field.type === "integer" ? "number" : "text"}
          step={field.type === "integer" ? 1 : undefined}
          autoComplete={secret ? "new-password" : "off"}
          value={String(value ?? "")}
          required={required}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          className="input-soft mt-1"
        />
      )}
      {secret ? <span className="mt-0.5 block font-normal text-ink-subtle">{saved ? t("secretKept") : t("secretHint")}</span> : null}
      {field.description ? <span className="mt-0.5 block font-normal text-ink-muted">{field.description}</span> : null}
    </label>
  );
}

function initialValue(field: AinaConfigField, current: unknown): ConfigValue {
  const value = current ?? field.default;
  if (field.type === "boolean") return value === true;
  return value === undefined || value === null ? "" : String(value);
}

function safeOrigin(url: string): string {
  try {
    return new URL(url).origin;
  } catch {
    return url;
  }
}
