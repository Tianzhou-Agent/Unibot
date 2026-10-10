import { useTranslation } from "react-i18next";
import { ChevronRight, Lightbulb } from "lucide-react";
import { classNames } from "@/lib/utils";

// The model's thinking before a reply or a tool call, collapsed to one line like a tool call.
export function ReasoningBlock({ text, active = false, compact = false }: {
  text: string;
  active?: boolean;
  compact?: boolean;
}) {
  const { t } = useTranslation("chat");
  // One line of the whole thinking; when it overflows, the start is cut so the newest words stay visible.
  const preview = text.replace(/\s+/g, " ").trim();

  return (
    <details className="group" aria-label={t("reasoning.aria")}>
      <summary className={classNames(
        "flex max-w-full cursor-pointer list-none items-center gap-1.5 rounded-md text-ink-muted marker:hidden hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-ring [&::-webkit-details-marker]:hidden",
        compact ? "min-h-6 text-[12px]" : "min-h-7 text-[13px]",
      )}>
        <Lightbulb className={classNames("shrink-0 text-ink-subtle", compact ? "h-3.5 w-3.5" : "h-4 w-4")} />
        <span className={classNames("shrink-0", active && "thinking-shimmer")}>
          {active ? t("reasoning.active") : t("reasoning.done")}
        </span>
        {preview ? (
          <>
            <span className="mx-1 h-0.5 w-0.5 shrink-0 rounded-full bg-ink-subtle group-open:hidden" />
            {/* rtl moves the ellipsis to the left; the inner ltr span keeps the text itself in reading order */}
            <span className="min-w-0 truncate text-left text-ink-subtle [direction:rtl] group-open:hidden" title={preview}>
              <span className="[direction:ltr] [unicode-bidi:isolate]">{preview}</span>
            </span>
          </>
        ) : null}
        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-subtle opacity-0 transition group-open:rotate-90 group-open:opacity-100 group-hover:opacity-100" />
      </summary>
      <div className={classNames(
        "mb-1.5 mt-1 whitespace-pre-wrap border-l-2 border-line pl-3 leading-[1.6] text-ink-subtle",
        compact ? "text-[12px]" : "text-[13px]",
      )}>
        {text.trim()}
      </div>
    </details>
  );
}
