import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Brain, MessageCircle, Plus, Search, Trash2, X } from "lucide-react";
import { api, apiErrorMessage } from "@/lib/api";
import { classNames } from "@/lib/utils";
import type { MemoryCategory, MemoryListResponse, MemoryRecord, MemoryStatsResponse } from "@/types";

const ACTOR = { user_id: "anonymous", tenant_id: "default" };
const CATEGORIES: Array<{ value: MemoryCategory; label: string }> = [
  { value: "fact", label: "事实" },
  { value: "preference", label: "偏好" },
  { value: "goal", label: "目标" },
  { value: "instruction", label: "指令" },
];

export function MemoryWidget({ disabled = false, onPrompt }: { disabled?: boolean; onPrompt?: (prompt: string) => void }) {
  const [items, setItems] = useState<MemoryRecord[]>([]);
  const [stats, setStats] = useState<MemoryStatsResponse | null>(null);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<MemoryCategory | "all">("all");
  const [content, setContent] = useState("");
  const [newCategory, setNewCategory] = useState<MemoryCategory>("fact");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams(ACTOR);
      if (query.trim()) params.set("q", query.trim());
      if (category !== "all") params.set("category", category);
      const [list, summary] = await Promise.all([
        api.get<MemoryListResponse>(`/memories?${params}`),
        api.get<MemoryStatsResponse>(`/memories/stats?${new URLSearchParams(ACTOR)}`),
      ]);
      setItems(list.items);
      setStats(summary);
      setError(null);
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [category, query]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 180);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function save(event: FormEvent) {
    event.preventDefault();
    const value = content.trim();
    if (!value || saving || disabled) return;
    setSaving(true);
    try {
      await api.post<MemoryRecord>("/memories", {
        ...ACTOR,
        content: value,
        category: newCategory,
        metadata: { write_origin: "memory-widget" },
      });
      setContent("");
      await load();
    } catch (saveError) {
      setError(apiErrorMessage(saveError));
    } finally {
      setSaving(false);
    }
  }

  async function remove(memoryId: string) {
    try {
      await api.delete(`/memories/${memoryId}?${new URLSearchParams(ACTOR)}`);
      setPendingDelete(null);
      await load();
    } catch (deleteError) {
      setError(apiErrorMessage(deleteError));
    }
  }

  return (
    <div className="space-y-4">
      <form onSubmit={save} className="rounded-xl border border-line bg-app-soft p-3.5">
        <div className="flex items-center gap-2">
          <Brain className="h-4 w-4 text-accent" />
          <h4 className="text-[13px] font-extrabold text-ink">添加长期记忆</h4>
          <span className="ml-auto text-[10px] text-ink-muted">去重 · 安全扫描 · 跨对话召回</span>
        </div>
        <textarea
          value={content}
          onChange={(event) => setContent(event.target.value)}
          disabled={disabled || saving}
          rows={3}
          aria-label="新记忆"
          placeholder="例如：我偏好简洁的中文回答"
          className="input-soft mt-3 resize-y text-[12.5px]"
        />
        <div className="mt-2.5 flex items-center gap-2">
          <select
            value={newCategory}
            onChange={(event) => setNewCategory(event.target.value as MemoryCategory)}
            disabled={disabled || saving}
            aria-label="记忆分类"
            className="h-9 rounded-lg border border-line bg-white px-3 text-[11.5px] font-semibold text-ink outline-none focus:border-accent"
          >
            {CATEGORIES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
          <button type="submit" disabled={disabled || saving || !content.trim()} className="btn-primary ml-auto disabled:opacity-50">
            <Plus className="h-4 w-4" />{saving ? "保存中…" : "保存记忆"}
          </button>
        </div>
      </form>

      <div className="grid grid-cols-5 gap-2">
        <Stat label="全部" value={stats?.total ?? 0} active={category === "all"} onClick={() => setCategory("all")} />
        {CATEGORIES.map((item) => (
          <Stat
            key={item.value}
            label={item.label}
            value={stats?.[item.value] ?? 0}
            active={category === item.value}
            onClick={() => setCategory(item.value)}
          />
        ))}
      </div>

      <label className="flex h-10 items-center gap-2 rounded-lg border border-line-strong bg-white px-3 focus-within:border-accent">
        <Search className="h-3.5 w-3.5 text-ink-muted" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          type="search"
          aria-label="搜索记忆"
          placeholder="搜索记忆内容"
          className="min-w-0 flex-1 bg-transparent text-[12px] outline-none placeholder:text-ink-subtle"
        />
        {query ? <button type="button" onClick={() => setQuery("")} aria-label="清除搜索"><X className="h-3.5 w-3.5 text-ink-muted" /></button> : null}
      </label>

      {error ? <p className="rounded-lg border border-danger-ring bg-danger-soft p-3 text-[11.5px] text-danger-deep">{error}</p> : null}
      {loading ? <div className="h-32 animate-pulse rounded-xl bg-line/60" /> : null}
      {!loading && !items.length ? (
        <div className="rounded-xl border border-dashed border-line py-12 text-center">
          <Brain className="mx-auto h-7 w-7 text-ink-subtle" />
          <p className="mt-2 text-[12px] font-semibold text-ink-muted">{query ? "没有匹配的记忆" : "还没有长期记忆"}</p>
        </div>
      ) : null}
      {!loading && items.length ? (
        <div className="space-y-2.5" aria-label="记忆列表">
          {items.map((memory) => (
            <article key={memory.id} className="rounded-xl border border-line bg-white p-3.5">
              <div className="flex items-start gap-3">
                <span className={classNames("rounded-md px-2 py-1 text-[9.5px] font-bold", categoryTone(memory.category))}>
                  {categoryLabel(memory.category)}
                </span>
                <p className="min-w-0 flex-1 text-[12.5px] leading-relaxed text-ink">{memory.content}</p>
              </div>
              <div className="mt-3 flex items-center gap-2 border-t border-line pt-2.5">
                <span className="font-mono text-[9.5px] text-ink-subtle">{memory.id}</span>
                <span className="ml-auto" />
                <button
                  type="button"
                  onClick={() => onPrompt?.(`请根据这条记忆继续对话：${memory.content}`)}
                  className="btn-ghost h-7 px-2 text-[10.5px]"
                >
                  <MessageCircle className="h-3.5 w-3.5" />在对话中询问
                </button>
                {pendingDelete === memory.id ? (
                  <>
                    <button type="button" onClick={() => setPendingDelete(null)} className="btn-outline h-7 px-2 text-[10.5px]">取消</button>
                    <button type="button" onClick={() => void remove(memory.id)} className="btn-danger-outline h-7 px-2 text-[10.5px]">确认删除</button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={() => setPendingDelete(memory.id)}
                    className="btn-ghost h-7 px-2 text-[10.5px] text-danger"
                    aria-label={`删除记忆 ${memory.content}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />删除
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function Stat({ label, value, active, onClick }: { label: string; value: number; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={classNames(
        "rounded-lg border px-2 py-2 text-left transition",
        active ? "border-accent bg-accent text-white" : "border-line bg-white hover:bg-app-soft",
      )}
    >
      <span className={classNames("block text-[9.5px] font-bold", active ? "text-white/75" : "text-ink-muted")}>{label}</span>
      <strong className="mt-0.5 block text-[15px]">{value}</strong>
    </button>
  );
}

function categoryLabel(category: MemoryCategory): string {
  return CATEGORIES.find((item) => item.value === category)?.label ?? category;
}

function categoryTone(category: MemoryCategory): string {
  if (category === "preference") return "bg-indigo-50 text-indigo-600";
  if (category === "goal") return "bg-success-soft text-success-deep";
  if (category === "instruction") return "bg-warning-soft text-warning-deep";
  return "bg-accent-soft text-accent";
}
