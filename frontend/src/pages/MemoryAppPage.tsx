import { useEffect, useMemo, useState } from "react";
import { Search, Trash2, X } from "lucide-react";
import { api } from "@/lib/api";
import { MEMORY_ITEMS, MEMORY_STATS } from "@/mocks/seed";
import type { MemoryItem, MemoryStats } from "@/types";
import { Topbar } from "@/components/layout/Topbar";
import { classNames } from "@/lib/utils";

type Category = "all" | "fact" | "goal" | "pending";

const FILTERS: Array<{ id: Category; label: string }> = [
  { id: "all", label: "全部" },
  { id: "fact", label: "事实" },
  { id: "goal", label: "目标" },
  { id: "pending", label: "待确认" },
];

export default function MemoryAppPage() {
  const [items, setItems] = useState<MemoryItem[]>(MEMORY_ITEMS);
  const [stats, setStats] = useState<MemoryStats>(MEMORY_STATS);
  const [category, setCategory] = useState<Category>("all");
  const [q, setQ] = useState("");

  useEffect(() => {
    api.get<MemoryStats>("/memories/stats").then(setStats).catch(() => {});
  }, []);

  const filtered = useMemo(() => {
    return items.filter((m) => {
      if (category !== "all" && m.category !== category) return false;
      if (q && !m.title.toLowerCase().includes(q.toLowerCase())) return false;
      return true;
    });
  }, [items, category, q]);

  async function handleAction(id: string, action: "keep" | "delete") {
    if (action === "delete") {
      await api.post(`/memories/${id}/delete`).catch(() => {});
      setItems((prev) => prev.filter((m) => m.id !== id));
    } else {
      await api.post(`/memories/${id}/keep`).catch(() => {});
    }
  }

  return (
    <div className="h-full flex flex-col bg-app-bg">
      <Topbar title="Memory 应用" badge={{ label: "应用模块", tone: "info" }} />
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="mx-auto max-w-[1640px] space-y-3">
          <div className="flex items-center gap-4">
            <div className="flex-1" />
            <label className="h-10 w-[412px] rounded-lg border border-line-strong bg-app-soft px-3 flex items-center gap-2 focus-within:border-accent">
              <Search className="w-3.5 h-3.5 text-ink-muted" />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                type="text"
                placeholder="搜索记忆"
                className="flex-1 bg-transparent text-[13px] placeholder:text-ink-subtle outline-none"
              />
              {q ? (
                <button type="button" onClick={() => setQ("")} aria-label="清空">
                  <X className="w-3.5 h-3.5 text-ink-muted" />
                </button>
              ) : null}
            </label>
          </div>
          <div className="grid grid-cols-4 gap-3">
            <StatCard label="全部记忆" value={stats.total} tone="accent" />
            <StatCard label="事实" value={stats.fact} />
            <StatCard label="目标" value={stats.goal} />
            <StatCard label="待确认" value={stats.pending} />
          </div>
          <div className="flex items-center gap-2">
            {FILTERS.map((f) => (
              <FilterChip
                key={f.id}
                active={category === f.id}
                onClick={() => setCategory(f.id)}
              >
                {f.label}
              </FilterChip>
            ))}
          </div>
          <div className="rounded-md border border-line bg-app-soft p-3.5 space-y-2.5">
            <div className="text-ink-muted text-[13px] font-extrabold">记忆列表</div>
            {filtered.length === 0 ? (
              <EmptyState />
            ) : (
              filtered.map((m) => (
                <MemoryRow key={m.id} item={m} onAction={handleAction} />
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value, tone }: { label: string; value: number; tone?: "accent" }) {
  return (
    <div className="rounded-md border border-line bg-app-soft p-3.5 space-y-1">
      <div
        className={classNames(
          "text-[24px] font-extrabold",
          tone === "accent" ? "text-accent" : "text-ink",
        )}
      >
        {value}
      </div>
      <div className="text-ink-muted text-[12px]">{label}</div>
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

function MemoryRow({
  item,
  onAction,
}: {
  item: MemoryItem;
  onAction: (id: string, action: "keep" | "delete") => void;
}) {
  return (
    <div
      className={classNames(
        "rounded-lg p-3 space-y-1.5 bg-white",
        item.selected ? "border border-accent-ring" : "border border-line",
      )}
    >
      <div className="text-ink text-[14px] font-bold">{item.title}</div>
      <div
        className={classNames(
          "text-[12px]",
          item.meta.includes("待确认") ? "text-warning-deep" : "text-ink-muted",
        )}
      >
        {item.meta}
      </div>
      <div className="flex items-center gap-3">
        <span
          className={classNames(
            "text-[12px]",
            item.sourceTone === "accent"
              ? "text-accent"
              : item.sourceTone === "warning"
                ? "text-warning"
                : "text-ink-muted",
          )}
        >
          {item.source}
        </span>
        <span className="flex-1" />
        <span className="text-[12px] text-danger font-extrabold">操作：</span>
        <button
          type="button"
          onClick={() => onAction(item.id, "keep")}
          className="text-success-deep text-[12px] font-extrabold hover:underline"
        >
          保留
        </button>
        <span className="text-ink-subtle text-[12px]">·</span>
        <button
          type="button"
          onClick={() => onAction(item.id, "delete")}
          className="inline-flex items-center gap-1 text-danger text-[12px] font-extrabold hover:underline"
        >
          <Trash2 className="w-3 h-3" />
          删除
        </button>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="py-10 text-center text-ink-muted text-[13px]">
      没有匹配的记忆。试试切换筛选或清空搜索词。
    </div>
  );
}
