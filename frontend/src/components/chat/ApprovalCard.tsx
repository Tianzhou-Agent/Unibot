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
  return (
    <section className="rounded-xl border border-warning-ring bg-warning-soft p-4" aria-label="授权确认">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-warning/15 text-warning">
          <ShieldAlert className="h-5 w-5" />
        </div>
        <div className="flex-1">
          <h2 className="text-[14px] font-extrabold text-warning-deep">高风险操作需要确认</h2>
          <p className="mt-1 text-[12.5px] text-warning-deep/80">
            智能体准备运行：{approval.capability_names.join("、")}。请核对参数后决定是否继续。
          </p>
          {debugMode ? (
            <div className="mt-3 space-y-2">
              {approval.tool_calls.map((call) => (
                <pre
                  key={call.id}
                  className="whitespace-pre-wrap break-all rounded-lg border border-warning-ring bg-white p-2.5 text-[10.5px] text-ink"
                >
                  {call.function.name}\n{formatJson(call.function.arguments)}
                </pre>
              ))}
            </div>
          ) : null}
          <div className="mt-3 flex items-center gap-2">
            <button type="button" disabled={disabled} onClick={onConfirm} className="btn-primary">
              确认并执行
            </button>
            <button type="button" disabled={disabled} onClick={onDeny} className="btn-outline">
              拒绝
            </button>
          </div>
        </div>
      </div>
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
