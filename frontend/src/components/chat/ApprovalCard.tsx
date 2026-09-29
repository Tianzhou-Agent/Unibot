import { useTranslation } from "react-i18next";
import { ShieldAlert } from "lucide-react";
import type { ApprovalRecord } from "@/types";

export function ApprovalCard({
  approval,
  disabled,
  debugMode,
  onConfirm,
  onDeny,
}: {
  approval: ApprovalRecord;
  disabled: boolean;
  debugMode: boolean;
  onConfirm: () => void;
  onDeny: () => void;
}) {
  const { t } = useTranslation("chat");
  return (
    <section className="rounded-xl border border-line-strong bg-white px-3.5 py-2.5" aria-label={t("approval.aria")}>
      <div className="flex flex-wrap items-center gap-2.5">
        <ShieldAlert className="h-4 w-4 shrink-0 text-warning" />
        <div className="min-w-[180px] flex-1">
          <h2 className="text-[13px] font-medium text-ink">{t("approval.title", { names: approval.capability_names.join(t("approval.joiner")) })}</h2>
          <p className="mt-0.5 text-[11.5px] leading-5 text-ink-subtle">
            {t("approval.summary", { count: approval.tool_calls.length })}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button type="button" disabled={disabled} onClick={onDeny} className="btn-outline h-8 px-3.5 text-[12px] disabled:opacity-50">
            {t("approval.deny")}
          </button>
          <button type="button" disabled={disabled} onClick={onConfirm} className="btn h-8 bg-ink px-3.5 text-[12px] text-white hover:bg-black disabled:opacity-50">
            {t("approval.allow")}
          </button>
        </div>
      </div>
      {debugMode ? (
        <details className="mt-2.5 border-t border-line pt-2.5">
          <summary className="cursor-pointer text-[10.5px] text-ink-subtle">{t("approval.viewArgs")}</summary>
          <div className="mt-2 space-y-2">
            {approval.tool_calls.map((call) => (
              <pre key={call.id} className="whitespace-pre-wrap break-all rounded-lg bg-app-soft p-2.5 text-[10px] leading-relaxed text-ink-muted">
                {call.function.name}\n{formatJson(call.function.arguments)}
              </pre>
            ))}
          </div>
        </details>
      ) : null}
    </section>
  );
}

function formatJson(value: string): string {
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}
