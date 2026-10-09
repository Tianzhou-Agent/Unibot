import { useTranslation } from "react-i18next";
import { useEffect, useRef, useState } from "react";
import { AppWindow, X } from "lucide-react";
import { classNames } from "@/lib/utils";
import type { WidgetDefinition } from "@/types";

/** Lets the user pick which of the agent's suggested AINAs to open beside the conversation. */
export function AinaChooserDialog({ widget, openAinaIds, onOpen, onClose }: {
  widget: WidgetDefinition;
  openAinaIds: readonly string[];
  onOpen: (ainaIds: string[]) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation("canvas");
  // The suggestions are ranked, so the best match not already open starts selected.
  const [selected, setSelected] = useState<string[]>(() => {
    const best = widget.apps.find((app) => !openAinaIds.includes(app.aina_id));
    return best ? [best.aina_id] : [];
  });
  const dialogRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    (dialog?.querySelector<HTMLElement>("input:not(:disabled)") ?? dialog?.querySelector<HTMLElement>("button"))?.focus();
    function onEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onEscape);
    return () => document.removeEventListener("keydown", onEscape);
  }, [onClose]);

  function toggle(ainaId: string) {
    setSelected((current) => current.includes(ainaId) ? current.filter((id) => id !== ainaId) : [...current, ainaId]);
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/30 p-4"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="aina-chooser-title"
        className="w-full max-w-md rounded-xl border border-line-strong bg-white p-4 shadow-soft"
      >
        <header className="flex items-start gap-2">
          <h2 id="aina-chooser-title" className="min-w-0 flex-1 text-[14px] font-semibold text-ink">{t("chooser.title")}</h2>
          <button type="button" onClick={onClose} className="btn-ghost h-7 px-2" aria-label={t("chooser.notNow")}>
            <X className="h-3.5 w-3.5" />
          </button>
        </header>
        <p className="mt-1 text-[12px] leading-relaxed text-ink-muted">{widget.description || t("chooser.defaultReason")}</p>
        <ul className="mt-3 space-y-2">
          {widget.apps.map((app) => {
            const alreadyOpen = openAinaIds.includes(app.aina_id);
            const checked = alreadyOpen || selected.includes(app.aina_id);
            return (
              <li key={app.aina_id}>
                <label className={classNames(
                  "flex cursor-pointer items-start gap-2.5 rounded-lg border p-2.5 transition",
                  checked ? "border-accent-ring bg-accent-soft" : "border-line hover:bg-app-soft",
                  alreadyOpen && "cursor-default opacity-70",
                )}>
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={alreadyOpen}
                    onChange={() => toggle(app.aina_id)}
                    className="mt-1 accent-accent"
                  />
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white text-accent shadow-sm">
                    <AppWindow className="h-4 w-4" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5">
                      <strong className="truncate text-[13px] text-ink">{app.name}</strong>
                      {alreadyOpen ? <span className="shrink-0 rounded bg-white px-1.5 py-0.5 text-[10px] text-ink-muted">{t("chooser.alreadyOpen")}</span> : null}
                    </span>
                    <span className="mt-0.5 line-clamp-2 block text-[11px] leading-[1.45] text-ink-muted">{app.description}</span>
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="btn-outline h-8">{t("chooser.notNow")}</button>
          <button
            type="button"
            disabled={!selected.length}
            onClick={() => onOpen(selected)}
            className="btn-primary h-8 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {t("chooser.open", { count: selected.length })}
          </button>
        </div>
      </div>
    </div>
  );
}
