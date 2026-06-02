import { useEffect, useState } from "react";
import { X, Search, Database, Trash2, Filter, ArrowRight } from "lucide-react";
import { api } from "@/lib/api";
import { CHAT_THREAD_SYSTEM_INTERACTION } from "@/mocks/seed";
import type { ChatThread, MemoryItem, MemoryStats } from "@/types";
import { Topbar } from "@/components/layout/Topbar";
import { AssistantMessage, Composer, UserMessage } from "@/components/chat/MessageBubble";
import { classNames } from "@/lib/utils";

type Category = "all" | "fact" | "goal" | "pending";

const FILTERS: Array<{ id: Category; label: string }> = [
  { id: "all", label: "全部" },
  { id: "fact", label: "事实" },
  { id: "goal", label: "目标" },
  { id: "pending", label: "待确认" },
];

export default function CanvasModePage() {
  const [thread, setThread] = useState<ChatThread>(CHAT_THREAD_SYSTEM_INTERACTION);
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [category, setCategory] = useState<Category>("all");

  useEffect(() => {
    api
      .get<ChatThread>("/sessions/sess_canvas_app/thread?kind=system")
      .then(setThread)
      .catch(() => {});
    api.get<{ items: MemoryItem[]; total: number }>("/memories").then((d) => setItems(d.items));
    api.get<MemoryStats>("/memories/stats").then(setStats).catch(() => {});
  }, []);

  const filtered = items.filter((m) => category === "all" || m.category === category);

  return (
    <div className="h-full flex flex-col bg-app-bg">
      <Topbar title="Memory 应用模块" badge={{ label: "画布模式", tone: "info" }} />
      <div className="flex-1 min-h-0">
        <div className="h-full grid grid-cols-[420px_1fr] gap-3">
          <NarrowChat thread={thread} />
          <CanvasPanel
            items={filtered}
            category={category}
            setCategory={setCategory}
            stats={stats}
          />
        </div>
      </div>
    </div>
  );
}

function NarrowChat({ thread }: { thread: ChatThread }) {
  return (
    <div className="rounded-lg border border-line bg-white flex flex-col overflow-hidden">
      <div className="h-[54px] px-4 flex items-center gap-3 border-b border-line bg-white">
        <span className="text-[16px] font-bold font-display">询问 Canvas 应用详情</span>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto bg-app-bg p-4 space-y-2.5">
        {thread.messages.map((m) =>
          m.role === "user" ? (
            <UserMessage key={m.id} content={m.content} files={m.files} />
          ) : (
            <AssistantMessage key={m.id} message={m} />
          ),
        )}
      </div>
      <div className="border-t border-line bg-white p-2.5">
        <Composer onSend={() => {}} />
      </div>
    </div>
  );
}

function CanvasPanel({
  items,
  category,
  setCategory,
  stats,
}: {
  items: MemoryItem[];
  category: Category;
  setCategory: (c: Category) => void;
  stats: MemoryStats | null;
}) {
  return (
    <div className="rounded-lg border border-line bg-white flex flex-col overflow-hidden">
      <div className="px-5 pt-4 pb-3 space-y-3 border-b border-line bg-app-soft">
        <div className="flex items-center gap-3">
          <button
            type="button"
            className="shrink-0 h-9 px-2.5 rounded-lg border border-line-strong bg-white inline-flex items-center gap-1.5 text-[13px] font-bold text-ink hover:bg-app-soft whitespace-nowrap"
          >
            <X className="w-3.5 h-3.5 text-ink-muted" />
            退出画布
          </button>
          <div>
            <h2 className="text-[24px] font-bold font-display">Memory 应用模块</h2>
            <p className="text-ink-muted text-[12.5px]">
              支持筛选、删除确认、新增和文件导入；保留历史，不直接编辑。
            </p>
          </div>
          <div className="flex-1" />
          <label className="h-9 w-[280px] rounded-lg border border-line-strong bg-white px-3 flex items-center gap-2 focus-within:border-accent">
            <Search className="w-3.5 h-3.5 text-ink-muted" />
            <input
              type="text"
              placeholder="搜索记忆"
              className="flex-1 bg-transparent text-[12.5px] placeholder:text-ink-subtle outline-none"
            />
          </label>
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
          <span className="flex-1" />
          {stats ? (
            <span className="text-ink-muted text-[12.5px]">
              共 {stats.total} 条 · 事实 {stats.fact} · 目标 {stats.goal} · 待确认 {stats.pending}
            </span>
          ) : null}
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-2.5">
        {items.slice(0, 3).map((m) => (
          <CanvasMemoryRow key={m.id} item={m} />
        ))}
      </div>
    </div>
  );
}

function CanvasMemoryRow({ item }: { item: MemoryItem }) {
  return (
    <div
      className={classNames(
        "rounded-md p-3 space-y-1.5 bg-white",
        item.selected ? "border border-accent-ring" : "border border-line",
      )}
    >
      <div className="flex items-center gap-2">
        <Database className="w-4 h-4 text-accent" />
        <span className="text-ink text-[14px] font-bold">{item.title}</span>
        <span className="flex-1" />
        <button className="text-ink-muted hover:text-ink" aria-label="筛选">
          <Filter className="w-3.5 h-3.5" />
        </button>
      </div>
      <div
        className={classNames(
          "text-[12px]",
          item.meta.includes("待确认") ? "text-warning-deep" : "text-ink-muted",
        )}
      >
        {item.meta}
      </div>
      <div className="flex items-center gap-2">
        <span
          className={classNames(
            "text-[12px]",
            item.sourceTone === "accent" ? "text-accent" : "text-ink-muted",
          )}
        >
          {item.source}
        </span>
        <span className="flex-1" />
        <span className="text-success-deep text-[12px] font-bold">保留</span>
        <span className="text-ink-subtle text-[12px]">·</span>
        <span className="inline-flex items-center gap-1 text-danger text-[12px] font-bold">
          <Trash2 className="w-3 h-3" />
          删除
        </span>
        <span className="text-ink-subtle text-[12px]">·</span>
        <span className="inline-flex items-center gap-1 text-accent text-[12px] font-bold">
          <ArrowRight className="w-3 h-3" />
          详情
        </span>
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
        active ? "bg-accent text-white border-transparent" : "bg-white text-ink border-line",
      )}
    >
      {children}
    </button>
  );
}
