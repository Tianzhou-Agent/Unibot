import { NavLink, useLocation } from "react-router-dom";
import {
  Search,
  Settings as SettingsIcon,
  LayoutGrid,
  AlertTriangle,
  CheckCircle2,
} from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { SessionSummary } from "@/types";
import { classNames, timeAgo } from "@/lib/utils";

type Filter = "all" | "running" | "pending" | "done";

const FILTERS: Array<{ id: Filter; label: string }> = [
  { id: "all", label: "全部" },
  { id: "running", label: "进行中" },
  { id: "pending", label: "待办" },
  { id: "done", label: "已完成" },
];

type BadgeTone = "blue" | "amber" | "green" | "muted";
const STATUS_BADGE: Record<NonNullable<SessionSummary["status"]>, { label: string; tone: BadgeTone } | null> = {
  active: { label: "进行中", tone: "blue" },
  running: { label: "进行中", tone: "blue" },
  pending: { label: "待办", tone: "amber" },
  done: { label: "已完成", tone: "green" },
  idle: null,
  archived: null,
};

export function Sidebar() {
  const [activeFilter, setActiveFilter] = useState<Filter>("all");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api
      .get<{ items: SessionSummary[] }>("/sessions")
      .then((data) => {
        if (!cancelled) {
          setSessions(data.items);
          setLoading(false);
        }
      })
      .catch(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = filterSessions(sessions, activeFilter);

  return (
    <aside className="w-[232px] shrink-0 h-full bg-sidebar-bg text-ink-onDark flex flex-col dark-scroll">
      <div className="px-4 pt-4 pb-3 flex items-center gap-2">
        <Brand />
        <button
          type="button"
          className="ml-auto w-7 h-7 rounded-lg hover:bg-sidebar-hover flex items-center justify-center text-ink-onDarkMuted"
          aria-label="折叠侧边栏"
        >
          <CollapseIcon className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="px-4 pb-2">
        <label className="flex items-center gap-2 h-8 rounded-lg px-2.5 bg-sidebar-bg border border-sidebar-border focus-within:border-accent">
          <Search className="w-3.5 h-3.5 text-ink-onDarkMuted" />
          <input
            type="text"
            placeholder="搜索对话"
            className="flex-1 bg-transparent text-[12.5px] placeholder:text-ink-onDarkMuted/70 text-ink-inverse outline-none"
          />
        </label>
      </div>

      <div className="px-4 pb-2">
        <button
          type="button"
          className="w-full flex items-center justify-center gap-1.5 h-9 rounded-lg bg-accent hover:bg-accent-hover text-white text-[13px] font-semibold transition-colors"
        >
          <span className="text-[15px] leading-none">+</span>
          新建对话
        </button>
      </div>

      <div className="px-4 pb-3">
        <div className="flex flex-wrap gap-1.5">
          {FILTERS.map(({ id, label }) => (
            <button
              key={id}
              type="button"
              onClick={() => setActiveFilter(id)}
              className={classNames(
                "h-7 px-2.5 inline-flex items-center rounded-lg text-[12.5px] font-semibold transition-colors border",
                activeFilter === id
                  ? "bg-sidebar-active border-transparent text-white"
                  : "bg-sidebar-bg border-sidebar-border text-ink-onDark hover:bg-sidebar-hover",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <nav className="flex-1 min-h-0 overflow-y-auto px-4 pb-4 space-y-2">
        {loading ? <SkeletonList /> : filtered.map((s) => <SessionCard key={s.id} session={s} />)}
      </nav>

      <FooterUtility />
    </aside>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2">
      <div className="w-7 h-7 rounded-lg bg-sidebar-active flex items-center justify-center">
        <Logo className="w-3.5 h-3.5 text-white" />
      </div>
      <span className="text-ink-inverse text-[15px] font-extrabold tracking-tight">天舟AI</span>
    </div>
  );
}

function SessionCard({ session }: { session: SessionSummary }) {
  const loc = useLocation();
  const isActive = loc.pathname === linkForSession(session);
  const badge = STATUS_BADGE[session.status];
  return (
    <NavLink
      to={linkForSession(session)}
      className={classNames(
        "block rounded-lg p-2.5 border transition-colors",
        isActive
          ? "bg-sidebar-active border-transparent"
          : "bg-sidebar-bg border-sidebar-border hover:bg-sidebar-hover",
      )}
    >
      <div className="flex items-center gap-1.5">
        <span className="flex-1 text-[13px] font-semibold text-ink-onDark truncate">
          {session.title}
        </span>
      </div>
      <div className="mt-1.5 flex items-center gap-1.5">
        {badge ? <StatusBadge tone={badge.tone} label={badge.label} active={isActive} /> : null}
        <span
          className={classNames(
            "ml-auto text-[10.5px]",
            isActive ? "text-white/80" : "text-ink-onDarkMuted/70",
          )}
        >
          {timeAgo(session.updatedAt)}
        </span>
      </div>
    </NavLink>
  );
}

function StatusBadge({
  tone,
  label,
  active,
}: {
  tone: BadgeTone;
  label: string;
  active?: boolean;
}) {
  if (tone === "amber") {
    return (
      <span
        className={classNames(
          "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10.5px] font-bold",
          active ? "bg-warning text-white" : "bg-warning/15 text-warning",
        )}
      >
        <AlertTriangle className="w-3 h-3" />
        {label}
      </span>
    );
  }
  if (tone === "green") {
    return (
      <span
        className={classNames(
          "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10.5px] font-bold",
          active ? "bg-success text-white" : "bg-success/15 text-success",
        )}
      >
        <CheckCircle2 className="w-3 h-3" />
        {label}
      </span>
    );
  }
  if (tone === "blue") {
    return (
      <span className="inline-flex items-center gap-1 text-[10.5px] font-bold text-accent">
        <span className="w-1.5 h-1.5 rounded-full bg-accent" />
        {label}
      </span>
    );
  }
  return null;
}

function SkeletonList() {
  return (
    <div className="space-y-2">
      {[0, 1, 2].map((i) => (
        <div key={i} className="rounded-lg p-2.5 bg-sidebar-bg border border-sidebar-border">
          <div className="h-3 w-32 rounded bg-white/5" />
          <div className="mt-1.5 h-2.5 w-20 rounded bg-white/5" />
        </div>
      ))}
    </div>
  );
}

function FooterUtility() {
  const loc = useLocation();
  return (
    <div className="border-t border-sidebar-border p-3 flex items-center gap-2">
      <FooterBtn
        to="/settings"
        label="设置"
        active={loc.pathname.startsWith("/settings")}
        icon={<SettingsIcon className="w-4 h-4" />}
      />
      <FooterBtn
        to="/apps"
        label="应用"
        active={loc.pathname.startsWith("/apps")}
        icon={<LayoutGrid className="w-4 h-4" />}
      />
    </div>
  );
}

function FooterBtn({
  to,
  label,
  active,
  icon,
}: {
  to: string;
  label: string;
  active?: boolean;
  icon: React.ReactNode;
}) {
  return (
    <NavLink
      to={to}
      aria-label={label}
      title={label}
      className={classNames(
        "w-9 h-9 rounded-lg flex items-center justify-center transition-colors",
        active
          ? "bg-sidebar-active text-white"
          : "bg-sidebar-bg text-ink-onDark hover:bg-sidebar-hover",
      )}
    >
      {icon}
    </NavLink>
  );
}

function linkForSession(s: SessionSummary): string {
  if (s.id === "sess_canvas_app") return "/chat";
  if (s.id === "sess_memory_audit") return "/todo";
  if (s.id === "sess_canvas_review") return "/system";
  if (s.id === "sess_sr_add") return "/todo";
  if (s.id === "sess_event_blocker") return "/system";
  if (s.id === "sess_app_calendar") return "/apps/memory";
  return "/chat";
}

function filterSessions(items: SessionSummary[], filter: Filter): SessionSummary[] {
  if (filter === "all") return items;
  return items.filter((i) => {
    if (filter === "running") return i.status === "active" || i.status === "running";
    if (filter === "pending") return i.status === "pending";
    if (filter === "done") return i.status === "done" || i.status === "idle";
    return true;
  });
}

function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M4 18V6h16v3H8v2.5h10V14H8v4z" fill="currentColor" />
    </svg>
  );
}

function CollapseIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M14 6l-6 6 6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
