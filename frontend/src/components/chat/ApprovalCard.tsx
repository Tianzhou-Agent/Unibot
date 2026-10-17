import { useState } from "react";
import { useTranslation } from "react-i18next";
import { PencilLine, ShieldAlert } from "lucide-react";
import { AinaWidgetFrame } from "@/components/widgets/AinaWidgetFrame";
import type { ApprovalRecord } from "@/types";

export type EditedArguments = Record<string, Record<string, unknown>>;

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
  onConfirm: (edited: EditedArguments) => void;
  onDeny: () => void;
}) {
  const { t } = useTranslation("chat");
  // Arguments the AINA's confirmation widgets proposed, by call id; the host validates them again on Allow.
  const [edited, setEdited] = useState<EditedArguments>({});
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
          <button type="button" disabled={disabled} onClick={() => onConfirm(edited)} className="btn h-8 bg-ink px-3.5 text-[12px] text-white hover:bg-black disabled:opacity-50">
            {t("approval.allow")}
          </button>
        </div>
      </div>
      {approval.widgets?.length ? (
        <div className="mt-2.5 space-y-2">
          {approval.widgets.map((widget) => (
            <div key={widget.call_id ?? widget.widget_id}>
              <AinaWidgetFrame
                widget={widget}
                onProposeArguments={(args) => {
                  if (widget.call_id) setEdited((current) => ({ ...current, [widget.call_id as string]: args }));
                }}
              />
              {widget.call_id && edited[widget.call_id] ? (
                <p className="mt-1 inline-flex items-center gap-1 text-[11px] font-semibold text-accent">
                  <PencilLine className="h-3 w-3" />{t("approval.edited")}
                </p>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
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
