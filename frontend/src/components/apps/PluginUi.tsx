import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Brain,
  CalendarClock,
  FileText,
  Image as ImageIcon,
  MoreHorizontal,
  TerminalSquare,
} from "lucide-react";
import { classNames } from "@/lib/utils";

const TILE_COLORS = [
  "bg-sky-500",
  "bg-violet-500",
  "bg-emerald-500",
  "bg-amber-500",
  "bg-rose-500",
  "bg-indigo-500",
  "bg-teal-500",
  "bg-orange-500",
];

const BUILTIN_ICONS: Record<string, typeof FileText> = {
  "unibot-documents": FileText,
  "unibot-scheduler": CalendarClock,
  "unibot-memory": Brain,
  "unibot-code-runner": TerminalSquare,
  "unibot-image-recognition": ImageIcon,
};

/** Manifests carry no icon, so each plugin gets a stable colored tile: a glyph for known built-ins, else its initial. */
export function AppIcon({ id, name, size = "md", icon }: { id: string; name: string; size?: "sm" | "md" | "lg"; icon?: React.ReactNode }) {
  const Glyph = BUILTIN_ICONS[id];
  const hash = [...id].reduce((total, char) => (total * 31 + char.charCodeAt(0)) >>> 0, 7);
  return (
    <span
      aria-hidden
      className={classNames(
        "flex shrink-0 items-center justify-center font-bold",
        icon ? "bg-app-soft text-ink-muted ring-1 ring-inset ring-line" : `${TILE_COLORS[hash % TILE_COLORS.length]} text-white`,
        size === "lg" ? "h-16 w-16 rounded-2xl text-[26px]" : size === "sm" ? "h-5 w-5 rounded-md text-[10px]" : "h-11 w-11 rounded-xl text-[17px]",
      )}
    >
      {icon ?? (Glyph ? <Glyph className={size === "lg" ? "h-8 w-8" : size === "sm" ? "h-3 w-3" : "h-5 w-5"} /> : [...name.trim()][0]?.toUpperCase() ?? "?")}
    </span>
  );
}

export type MenuItem = { label: string; onSelect: () => void; danger?: boolean; icon?: React.ReactNode };

/** A small dropdown: the trigger toggles it, a click outside or Escape closes it. */
export function ActionMenu({ label, items, trigger, triggerClassName, note }: {
  label: string;
  items: MenuItem[];
  trigger?: React.ReactNode;
  triggerClassName?: string;
  /** Muted text under the items, e.g. why some actions are missing. */
  note?: string;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) setOpen(false);
    }
    function onEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onEscape);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onEscape);
    };
  }, [open]);

  if (!items.length && !note) return null;
  return (
    <div ref={containerRef} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={trigger ? undefined : label}
        title={trigger ? undefined : label}
        className={triggerClassName ?? "flex h-9 w-9 items-center justify-center rounded-full text-ink-muted transition-colors hover:bg-app-soft hover:text-ink"}
      >
        {trigger ?? <MoreHorizontal className="h-4 w-4" />}
      </button>
      {open ? (
        <div role="menu" aria-label={label} className="absolute right-0 top-full z-40 mt-1 min-w-48 overflow-hidden rounded-xl border border-line bg-white py-1 shadow-soft">
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                item.onSelect();
              }}
              className={classNames(
                "flex w-full items-center gap-2.5 whitespace-nowrap px-3 py-2 text-left text-[13px] hover:bg-app-soft",
                item.danger ? "text-danger" : "text-ink",
              )}
            >
              {item.icon ? <span className={item.danger ? "text-danger" : "text-ink-subtle"}>{item.icon}</span> : null}
              {item.label}
            </button>
          ))}
          {note ? <p className={classNames("max-w-64 px-3 py-2 text-[11.5px] leading-relaxed text-ink-subtle", items.length > 0 && "border-t border-line")}>{note}</p> : null}
        </div>
      ) : null}
    </div>
  );
}

/** One borderless plugin line: icon, name and a one-line description, with actions on the right. */
export function PluginRow({ icon, name, description, to, badge, actions }: {
  icon: React.ReactNode;
  name: string;
  description: string;
  to?: string;
  badge?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  const body = (
    <>
      {icon}
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="truncate text-[14.5px] font-medium text-ink">{name}</span>
          {badge}
        </span>
        <span className="mt-0.5 block truncate text-[13px] text-ink-muted" title={description}>{description}</span>
      </span>
    </>
  );
  return (
    <article aria-label={name} className="flex min-w-0 items-center gap-3 rounded-xl px-2 py-2.5 transition-colors hover:bg-app-soft">
      {to ? <Link to={to} className="flex min-w-0 flex-1 items-center gap-3.5">{body}</Link> : <div className="flex min-w-0 flex-1 items-center gap-3.5">{body}</div>}
      {actions ? <div className="flex shrink-0 items-center gap-1">{actions}</div> : null}
    </article>
  );
}

export function PluginSection({ title, count, children }: { title: string; count?: number; children: React.ReactNode }) {
  return (
    <section aria-label={title}>
      <h2 className="mb-2 flex items-baseline gap-2 px-2 text-[16px] font-semibold text-ink">
        {title}
        {count !== undefined ? <span className="text-[13px] font-normal text-ink-subtle">{count}</span> : null}
      </h2>
      {children}
    </section>
  );
}

export function PluginChip({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "success" | "warning" }) {
  return (
    <span className={classNames(
      "shrink-0 rounded-full px-2 py-0.5 text-[10.5px] font-medium",
      tone === "success" ? "bg-success-soft text-success-deep" : tone === "warning" ? "bg-warning-soft text-warning-deep" : "bg-app-soft text-ink-muted",
    )}>
      {children}
    </span>
  );
}
