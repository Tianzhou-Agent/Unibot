import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search } from "lucide-react";
import { api } from "@/lib/api";
import { APPS } from "@/mocks/seed";
import type { AppDescriptor } from "@/types";
import { Topbar } from "@/components/layout/Topbar";
import { DynamicIcon } from "@/components/chat/SurfaceRenderer";
import { classNames } from "@/lib/utils";

type Filter = "all" | "enabled";

export default function AllAppsPage() {
  const [apps, setApps] = useState<AppDescriptor[]>(APPS);
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    api.get<{ items: AppDescriptor[] }>("/apps").then((d) => setApps(d.items)).catch(() => {});
  }, []);

  const filtered = useMemo(() => {
    return apps.filter((a) => {
      if (filter === "enabled" && !a.enabled) return false;
      if (q && !`${a.name}${a.description}`.toLowerCase().includes(q.toLowerCase())) return false;
      return true;
    });
  }, [apps, filter, q]);

  return (
    <div className="h-full flex flex-col bg-app-bg">
      <Topbar title="全部应用" badge={{ label: `${filtered.length} 个应用`, tone: "neutral" }} />
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="mx-auto max-w-[1640px] space-y-3">
          <div className="rounded-xl border border-line bg-white p-5 space-y-3">
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-2">
                <FilterChip active={filter === "all"} onClick={() => setFilter("all")}>
                  全部
                </FilterChip>
                <FilterChip active={filter === "enabled"} onClick={() => setFilter("enabled")}>
                  已启用
                </FilterChip>
              </div>
              <div className="flex-1" />
              <label className="h-10 w-[280px] rounded-lg border border-line-strong bg-app-soft px-3 flex items-center gap-2 focus-within:border-accent">
                <Search className="w-3.5 h-3.5 text-ink-muted" />
                <input
                  type="text"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="搜索应用"
                  className="flex-1 bg-transparent text-[13px] placeholder:text-ink-subtle outline-none"
                />
              </label>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            {filtered.map((a) => (
              <AppCard
                key={a.id}
                app={a}
                onOpen={() => {
                  if (a.routesTo) navigate(a.routesTo);
                }}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={classNames(
        "h-8 px-3 rounded-lg text-[13px] font-bold border",
        active ? "bg-accent text-white border-transparent" : "bg-app-soft text-ink border-line",
      )}
    >
      {children}
    </button>
  );
}

function AppCard({
  app,
  onOpen,
}: {
  app: AppDescriptor;
  onOpen: () => void;
}) {
  const toneToText: Record<AppDescriptor["tone"], string> = {
    blue: "text-accent",
    green: "text-success",
    indigo: "text-indigo-500",
    amber: "text-warning",
    slate: "text-ink-muted",
  };
  return (
    <div className="rounded-md border border-line bg-white p-4 space-y-2.5">
      <div className="flex items-center gap-2.5">
        <DynamicIcon name={app.icon} className={classNames("w-5 h-5", toneToText[app.tone])} />
        <span className="text-ink text-[17px] font-bold">{app.name}</span>
      </div>
      <p className="text-ink-muted text-[13px] leading-[1.45]">{app.description}</p>
      <div className="flex items-center gap-2">
        <span className="px-1.5 py-0.5 rounded-md bg-app-soft text-ink-muted text-[10.5px] font-bold">
          {app.category === "system" ? "系统" : app.category === "builtin" ? "内置" : "扩展"}
        </span>
        <span className="flex-1" />
        <button
          type="button"
          onClick={onOpen}
          className="h-8 px-3 rounded-lg bg-accent text-white text-[12.5px] font-bold hover:bg-accent-hover"
        >
          打开
        </button>
      </div>
    </div>
  );
}
