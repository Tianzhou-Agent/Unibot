import { useEffect, useRef, useState, type PointerEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  AppWindow,
  ArrowLeft,
  ArrowRight,
  Brain,
  FileText,
  LayoutGrid,
  ListFilter,
  Maximize2,
  Minimize2,
  Plus,
  SquareTerminal,
  X,
} from "lucide-react";
import { classNames } from "@/lib/utils";

export const NEW_CANVAS_TAB = "__new-tab__";

export function CanvasTabs({ tabs, activeId, expanded, onSelect, onClose, onReorder, onNew, onExpand }: {
  tabs: { id: string; label: string; kind?: string }[];
  activeId: string | null;
  expanded: boolean;
  onSelect: (id: string) => void;
  onClose: (id: string) => void;
  onReorder: (ids: string[]) => void;
  onNew: () => void;
  onExpand: () => void;
}) {
  const { t } = useTranslation("canvas");
  const [menuOpen, setMenuOpen] = useState(false);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [drop, setDrop] = useState<{ id: string; after: boolean } | null>(null);
  const stripRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ id: string; x: number; y: number; moved: boolean } | null>(null);
  const dropRef = useRef(drop);
  const suppressClickRef = useRef(false);

  function cancelDrag() {
    dragRef.current = null;
    dropRef.current = null;
    setDraggingId(null);
    setDrop(null);
  }

  useEffect(() => {
    if (!menuOpen && !draggingId) return;
    function outside(event: globalThis.PointerEvent) {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    }
    function escape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      cancelDrag();
    }
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [menuOpen, draggingId]);

  useEffect(() => {
    document.getElementById(`canvas-tab-${activeId}`)?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [activeId, tabs.length]);

  function move(id: string, offset: number) {
    const ids = tabs.map((tab) => tab.id);
    const from = ids.indexOf(id);
    const to = from + offset;
    if (to < 0 || to >= ids.length) return;
    ids.splice(from, 1);
    ids.splice(to, 0, id);
    onReorder(ids);
  }

  function drag(event: PointerEvent<HTMLDivElement>) {
    const current = dragRef.current;
    if (!current) return;
    if (event.buttons !== 1) { cancelDrag(); return; }
    if (!current.moved && Math.hypot(event.clientX - current.x, event.clientY - current.y) < 6) return;
    if (!current.moved) {
      current.moved = true;
      suppressClickRef.current = true;
      event.currentTarget.setPointerCapture(event.pointerId);
      setDraggingId(current.id);
    }
    const strip = stripRef.current!;
    const bounds = strip.getBoundingClientRect();
    if (event.clientX < bounds.left + 24) strip.scrollLeft -= 16;
    if (event.clientX > bounds.right - 24) strip.scrollLeft += 16;
    const target = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>("[data-canvas-tab]");
    let next = null;
    if (target && strip.contains(target) && target.dataset.canvasTab !== current.id) {
      const rect = target.getBoundingClientRect();
      next = { id: target.dataset.canvasTab!, after: event.clientX > rect.left + rect.width / 2 };
    }
    dropRef.current = next;
    setDrop(next);
  }

  function finishDrag() {
    const current = dragRef.current;
    const target = dropRef.current;
    if (current?.moved && target) {
      const ids = tabs.map((tab) => tab.id).filter((id) => id !== current.id);
      ids.splice(ids.indexOf(target.id) + (target.after ? 1 : 0), 0, current.id);
      onReorder(ids);
    }
    cancelDrag();
  }

  const iconButton = "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-ink-muted transition-colors hover:bg-app-soft hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent";
  return (
    <div className="relative z-10 flex min-h-[51px] shrink-0 items-center gap-1 border-b border-line bg-white p-1.5">
      <div ref={stripRef} role="tablist" aria-label={t("tabs")} className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto px-1 py-0.5">
        {tabs.map((tab, index) => {
          const active = tab.id === activeId;
          const Icon = tab.id === NEW_CANVAS_TAB ? LayoutGrid : tab.id === "unibot-code-runner" ? SquareTerminal : tab.kind === "document" ? FileText : tab.kind === "memory" ? Brain : AppWindow;
          return (
            <div
              key={tab.id}
              data-canvas-tab={tab.id}
              onPointerDown={(event) => {
                if (event.button !== 0 || (event.target as HTMLElement).closest("[data-close-tab]")) return;
                suppressClickRef.current = false;
                dragRef.current = { id: tab.id, x: event.clientX, y: event.clientY, moved: false };
              }}
              onPointerMove={drag}
              onPointerUp={finishDrag}
              onPointerCancel={cancelDrag}
              className={classNames(
                "relative flex h-9 min-w-[100px] max-w-[190px] flex-1 touch-pan-y select-none items-center gap-1 rounded-lg pr-1 transition-colors",
                active ? "bg-accent-softer text-accent" : "text-ink-muted hover:bg-app-soft",
                draggingId === tab.id && "cursor-grabbing opacity-40",
              )}
            >
              {drop?.id === tab.id ? <span aria-hidden className={classNames("pointer-events-none absolute inset-y-1 z-10 w-0.5 rounded-full bg-accent", drop.after ? "-right-0.5" : "-left-0.5")} /> : null}
              <button
                type="button"
                role="tab"
                id={`canvas-tab-${tab.id}`}
                aria-controls={`canvas-panel-${tab.id}`}
                aria-selected={active}
                tabIndex={active ? 0 : -1}
                title={`${tab.label} · ${t("reorderShortcut")}`}
                onClick={() => { if (!suppressClickRef.current) onSelect(tab.id); }}
                onKeyDown={(event) => {
                  suppressClickRef.current = false;
                  if (event.altKey && (event.key === "ArrowLeft" || event.key === "ArrowRight")) {
                    event.preventDefault();
                    move(tab.id, event.key === "ArrowLeft" ? -1 : 1);
                    const button = event.currentTarget;
                    requestAnimationFrame(() => button.focus());
                  } else if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
                    event.preventDefault();
                    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowLeft" ? -1 : 1) + tabs.length) % tabs.length;
                    onSelect(tabs[next].id);
                    document.getElementById(`canvas-tab-${tabs[next].id}`)?.focus();
                  }
                }}
                className="flex h-full min-w-0 flex-1 cursor-grab items-center gap-2 rounded-lg pl-2.5 text-left text-[12px] focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
              >
                <Icon aria-hidden className="h-3.5 w-3.5 shrink-0" />
                <span className={classNames("truncate", active && "font-medium")}>{tab.label}</span>
              </button>
              <button type="button" data-close-tab onClick={() => onClose(tab.id)} className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-ink-subtle hover:bg-line/60 hover:text-ink" aria-label={t("close", { name: tab.label })}>
                <X className="h-3 w-3" />
              </button>
            </div>
          );
        })}
      </div>
      <button type="button" onClick={onNew} className={iconButton} aria-label={t("launcher.newTab")} title={t("launcher.newTab")}><Plus className="h-4 w-4" /></button>
      <div ref={menuRef} className="flex shrink-0 items-center border-l border-line pl-1">
        <button type="button" onClick={() => setMenuOpen((open) => !open)} className={iconButton} aria-label={t("tabMenu")} title={t("tabMenu")} aria-expanded={menuOpen}><ListFilter className="h-4 w-4" /></button>
        <button type="button" onClick={onExpand} className={iconButton} aria-label={t(expanded ? "restore" : "expand")} title={t(expanded ? "restore" : "expand")} aria-pressed={expanded}>
          {expanded ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
        </button>
        {menuOpen ? (
          <div role="group" aria-label={t("tabMenu")} className="absolute right-2 top-full z-20 mt-1 max-h-80 w-64 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-xl border border-line bg-white p-2 shadow-soft">
            {tabs.map((tab, index) => (
              <div key={tab.id} className="flex items-center gap-1 rounded-lg p-1 hover:bg-app-soft">
                <button type="button" onClick={() => { onSelect(tab.id); setMenuOpen(false); }} className={classNames("min-w-0 flex-1 truncate px-1 text-left text-[12px]", tab.id === activeId && "font-medium text-accent")} title={tab.label}>{tab.label}</button>
                <button type="button" onClick={() => move(tab.id, -1)} disabled={index === 0} className={classNames(iconButton, "disabled:opacity-30")} aria-label={t("moveLeft", { name: tab.label })}><ArrowLeft className="h-3.5 w-3.5" /></button>
                <button type="button" onClick={() => move(tab.id, 1)} disabled={index === tabs.length - 1} className={classNames(iconButton, "disabled:opacity-30")} aria-label={t("moveRight", { name: tab.label })}><ArrowRight className="h-3.5 w-3.5" /></button>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
