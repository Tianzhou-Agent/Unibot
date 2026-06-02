import { CheckCircle2, Info, Loader2, AlertTriangle, ClipboardList } from "lucide-react";
import { classNames } from "@/lib/utils";
import type { ChoiceOption, SurfaceBlock } from "@/types";

export function SurfaceRenderer({ block, onAction }: { block: SurfaceBlock; onAction?: (id: string) => void }) {
  switch (block.kind) {
    case "choices":
      return <ChoicesBlock intro={block.intro} options={block.options} onPick={onAction} />;
    case "confirm":
      return <ConfirmBlock block={block} onAction={onAction} />;
    case "status":
      return <StatusBlock text={block.text} tone={block.tone} />;
    case "error":
      return <ErrorBlock text={block.text} />;
    case "loading":
      return <LoadingBlock text={block.text} />;
    case "form":
      return <FormBlock block={block} />;
  }
}

function ChoicesBlock({
  intro,
  options,
  onPick,
}: {
  intro?: string;
  options: ChoiceOption[];
  onPick?: (id: string) => void;
}) {
  return (
    <div className="space-y-2">
      {intro ? (
        <p className="text-[13px] leading-[1.5] text-ink">{intro}</p>
      ) : null}
      <div className="space-y-2">
        {options.map((opt) => (
          <button
            key={opt.id}
            type="button"
            onClick={() => onPick?.(opt.id)}
            className={classNames(
              "w-full flex items-center gap-2.5 rounded-lg border h-12 px-2 text-left transition-colors",
              opt.selected
                ? "bg-accent border-accent"
                : "bg-white border-line hover:bg-app-soft",
            )}
          >
            <span
              className={classNames(
                "w-8 h-8 rounded-lg flex items-center justify-center",
                toneToBg(opt.iconTone),
              )}
            >
              <ChoiceIcon icon={opt.icon} tone={opt.iconTone} selected={opt.selected} />
            </span>
            <div className="flex-1 min-w-0">
              <div
                className={classNames(
                  "text-[15px] font-bold truncate",
                  opt.selected ? "text-white" : "text-ink",
                )}
              >
                {opt.title}
              </div>
              <div
                className={classNames(
                  "text-[10.5px]",
                  opt.selected ? "text-white/80" : "text-ink-muted",
                )}
              >
                {opt.description}
              </div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function ConfirmBlock({
  block,
  onAction,
}: {
  block: Extract<SurfaceBlock, { kind: "confirm" }>;
  onAction?: (id: string) => void;
}) {
  const tone = block.tone ?? "warning";
  return (
    <div
      className={classNames(
        "rounded-lg border p-3 flex items-start gap-3",
        tone === "warning" ? "bg-warning-soft border-warning-ring" : "bg-accent-soft border-accent-ring",
      )}
    >
      <div
        className={classNames(
          "w-8 h-8 rounded-lg flex items-center justify-center",
          tone === "warning" ? "bg-warning-ring" : "bg-accent-softer",
        )}
      >
        <Info
          className={classNames(
            "w-4 h-4",
            tone === "warning" ? "text-warning" : "text-accent",
          )}
        />
      </div>
      <div className="flex-1 min-w-0">
        <div
          className={classNames(
            "text-[13px] font-extrabold",
            tone === "warning" ? "text-warning-deep" : "text-ink",
          )}
        >
          {block.title}
        </div>
        <div
          className={classNames(
            "text-[12px]",
            tone === "warning" ? "text-warning-deep" : "text-ink-muted",
          )}
        >
          {block.description}
        </div>
      </div>
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => onAction?.("cancel")}
          className="h-7 px-2.5 rounded-lg border border-warning-ring bg-white text-warning-deep text-[12px] font-bold"
        >
          {block.cancelLabel ?? "取消"}
        </button>
        <button
          type="button"
          onClick={() => onAction?.("confirm")}
          className={classNames(
            "h-7 px-2.5 rounded-lg text-white text-[12px] font-bold",
            tone === "warning" ? "bg-warning" : "bg-accent",
          )}
        >
          {block.confirmLabel ?? "确认"}
        </button>
      </div>
    </div>
  );
}

function StatusBlock({ text, tone }: { text: string; tone: "info" | "success" }) {
  return (
    <div className="rounded-lg border bg-accent-soft border-accent-ring h-10 px-3 flex items-center gap-2.5">
      {tone === "success" ? (
        <CheckCircle2 className="w-4 h-4 text-success" />
      ) : (
        <Loader2 className="w-4 h-4 text-accent" />
      )}
      <span className="text-accent-hover text-[13px] font-semibold">{text}</span>
    </div>
  );
}

function ErrorBlock({ text }: { text: string }) {
  return (
    <div className="rounded-lg border bg-danger-soft border-danger-ring h-10 px-3 flex items-center gap-2.5">
      <AlertTriangle className="w-4 h-4 text-danger" />
      <span className="text-danger text-[13px] font-semibold">{text}</span>
    </div>
  );
}

function LoadingBlock({ text }: { text?: string }) {
  return (
    <div className="rounded-lg border bg-accent-soft border-accent-ring h-10 px-3 flex items-center gap-2.5">
      <Loader2 className="w-4 h-4 text-accent animate-spin" />
      <span className="text-accent-hover text-[13px] font-semibold">
        {text ?? "正在思考"}
      </span>
    </div>
  );
}

function FormBlock({ block }: { block: Extract<SurfaceBlock, { kind: "form" }> }) {
  return (
    <div className="rounded-lg border border-line bg-white p-3 space-y-2.5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ClipboardList className="w-4 h-4 text-indigo-500" />
          <span className="text-[14px] font-bold text-ink">{block.title}</span>
        </div>
        {block.hint ? (
          <span className="text-[12px] text-ink-muted">{block.hint}</span>
        ) : null}
      </div>
      <div className="space-y-2">
        {block.fields.map((f) => (
          <div key={f.id} className="rounded-lg border border-line bg-app-soft p-2.5 space-y-1.5">
            <label className="text-[12px] font-bold text-ink">{f.label}</label>
            <input
              type="text"
              placeholder={f.placeholder}
              defaultValue={f.value ?? ""}
              className="w-full h-8 px-2.5 rounded-lg border border-line bg-white text-[12px] placeholder:text-ink-subtle focus:outline-none focus:ring-2 focus:ring-accent-ring"
            />
          </div>
        ))}
      </div>
      <div className="flex items-center justify-between">
        <span className="text-[12px] text-ink-muted">填写后 Agent 将继续创建该需求。</span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="h-8 px-3 rounded-lg text-danger text-[12px] font-bold"
          >
            {block.cancelLabel ?? "取消"}
          </button>
          <button
            type="button"
            className="h-8 px-3 rounded-lg bg-accent text-white text-[12px] font-bold flex items-center gap-1.5"
          >
            {block.submitLabel ?? "提交"}
          </button>
        </div>
      </div>
    </div>
  );
}

function toneToBg(tone: "blue" | "green" | "indigo" | "slate"): string {
  if (tone === "green") return "bg-green-50";
  if (tone === "indigo") return "bg-indigo-50";
  if (tone === "slate") return "bg-app-soft";
  return "bg-accent-soft";
}

function ChoiceIcon({
  icon,
  tone,
  selected,
}: {
  icon: string;
  tone: "blue" | "green" | "indigo" | "slate";
  selected?: boolean;
}) {
  const colorClass = selected
    ? "text-white"
    : tone === "green"
      ? "text-success"
      : tone === "indigo"
        ? "text-indigo-500"
        : tone === "slate"
          ? "text-ink-muted"
          : "text-accent";
  return <DynamicIcon name={icon} className={classNames("w-[18px] h-[18px]", colorClass)} />;
}

import * as Lucide from "lucide-react";
export function DynamicIcon({ name, className }: { name: string; className?: string }) {
  const lib = Lucide as unknown as Record<string, React.FC<{ className?: string }>>;
  const Cmp = lib[name] ?? lib.Box;
  return <Cmp className={className} />;
}
