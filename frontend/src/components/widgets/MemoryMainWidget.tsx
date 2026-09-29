import { useTranslation } from "react-i18next";
import i18n, { currentLocale } from "@/i18n";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Brain, MessageCircle, Plus, Search, Trash2, X } from "lucide-react";
import { api, apiErrorMessage } from "@/lib/api";
import { classNames } from "@/lib/utils";
import type { MemoryCategory, MemoryListResponse, MemoryRecord, MemoryStatsResponse } from "@/types";

const ACTOR = { user_id: "anonymous", tenant_id: "default" };
const CATEGORIES: Array<{ value: MemoryCategory; label: string }> = [
  { value: "fact", get label() { return i18n.t("memory:cat.fact"); } },
  { value: "preference", get label() { return i18n.t("memory:cat.preference"); } },
  { value: "goal", get label() { return i18n.t("memory:cat.goal"); } },
  { value: "instruction", get label() { return i18n.t("memory:cat.instruction"); } },
];

export function MemoryMainWidget({
  disabled = false,
  onPrompt,
}: {
  disabled?: boolean;
  onPrompt?: (prompt: string) => void;
}) {
  const { t } = useTranslation("memory");
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
        metadata: { write_origin: "memory-main-widget" },
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
    <div className="h-full min-h-0 overflow-y-auto bg-white md:grid md:grid-cols-[220px_minmax(0,1fr)] md:overflow-hidden">
      <aside className="border-b border-line bg-app-soft md:flex md:min-h-0 md:flex-col md:border-b-0 md:border-r">
        <form onSubmit={save} className="border-b border-line p-3">
          <div className="flex items-center gap-2">
            <Brain className="h-4 w-4 text-accent" />
            <h2 className="text-[12.5px] font-extrabold text-ink">{t("add")}</h2>
          </div>
          <textarea
            value={content}
            onChange={(event) => setContent(event.target.value)}
            disabled={disabled || saving}
            rows={4}
            aria-label={t("newAria")}
            placeholder={t("newPlaceholder")}
            className="input-soft mt-2.5 resize-none bg-white text-[11.5px]"
          />
          <div className="mt-2 flex items-center gap-1.5">
            <select
              value={newCategory}
              onChange={(event) => setNewCategory(event.target.value as MemoryCategory)}
              disabled={disabled || saving}
              aria-label={t("categoryAria")}
              className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-white px-2.5 text-[11px] font-semibold text-ink outline-none focus:border-accent"
            >
              {CATEGORIES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
            <button
              type="submit"
              disabled={disabled || saving || !content.trim()}
              className="btn-primary h-9 w-9 shrink-0 p-0 disabled:opacity-50"
              aria-label={t("saveAria")}
              title={t("saveAria")}
            >
              <Plus className="h-4 w-4" />
            </button>
          </div>
        </form>

        <nav className="grid grid-cols-5 gap-1.5 p-2 md:block md:min-h-0 md:flex-1 md:space-y-1 md:overflow-y-auto" aria-label={t("categoryAria")}>
          <CategoryFilter label={t("all")} value={stats?.total ?? 0} active={category === "all"} onClick={() => setCategory("all")} />
          {CATEGORIES.map((item) => (
            <CategoryFilter
              key={item.value}
              label={item.label}
              value={stats?.[item.value] ?? 0}
              active={category === item.value}
              onClick={() => setCategory(item.value)}
            />
          ))}
        </nav>
      </aside>

      <section className="flex min-h-0 min-w-0 flex-col">
        <header className="flex min-h-14 flex-wrap items-center gap-3 border-b border-line px-3 py-2">
          <div className="min-w-0 flex-1">
            <h2 className="text-[13.5px] font-extrabold text-ink">{t("longTerm")}</h2>
            <p className="text-[10.5px] text-ink-muted">{t("results", { count: items.length })}</p>
          </div>
          <label className="flex h-9 w-full items-center gap-2 rounded-lg border border-line-strong bg-app-soft px-3 focus-within:border-accent sm:w-64">
            <Search className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              type="search"
              aria-label={t("searchAria")}
              placeholder={t("searchAria")}
              className="min-w-0 flex-1 bg-transparent text-[11.5px] outline-none placeholder:text-ink-subtle"
            />
            {query ? <button type="button" onClick={() => setQuery("")} aria-label={t("clearSearch")} title={t("clearSearch")}><X className="h-3.5 w-3.5 text-ink-muted" /></button> : null}
          </label>
        </header>

        {error ? (
          <div className="flex items-center gap-2 border-b border-danger-ring bg-danger-soft px-3 py-2 text-[10.5px] text-danger-deep">
            <span className="min-w-0 flex-1">{error}</span>
            <button type="button" onClick={() => setError(null)} aria-label={t("closeError")} title={t("close")}><X className="h-3.5 w-3.5" /></button>
          </div>
        ) : null}

        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {loading ? <div className="h-28 animate-pulse rounded-lg bg-line/60" /> : null}
          {!loading && !items.length ? (
            <div className="flex min-h-56 flex-col items-center justify-center text-center text-ink-muted">
              <Brain className="h-7 w-7 text-ink-subtle" />
              <p className="mt-2 text-[12px] font-semibold">{query ? t("noMatch") : t("noMemories")}</p>
            </div>
          ) : null}
          {!loading && items.length ? (
            <div className="grid gap-2.5 xl:grid-cols-2" aria-label={t("listAria")}>
              {items.map((memory) => (
                <article key={memory.id} className="flex min-h-[126px] flex-col rounded-lg border border-line bg-white p-3">
                  <div className="flex items-start gap-2.5">
                    <span className={classNames("shrink-0 rounded-md px-2 py-1 text-[9.5px] font-bold", categoryTone(memory.category))}>
                      {categoryLabel(memory.category)}
                    </span>
                    <p className="min-w-0 flex-1 text-[12px] leading-relaxed text-ink">{memory.content}</p>
                  </div>
                  <div className="mt-auto flex items-center gap-1.5 border-t border-line pt-2.5">
                    <span className="text-[9.5px] text-ink-subtle">{formatDate(memory.updated_at)}</span>
                    <span className="ml-auto" />
                    <button
                      type="button"
                      onClick={() => onPrompt?.(t("askPrompt", { content: memory.content }))}
                      className="btn-ghost h-7 w-7 p-0"
                      aria-label={t("askInChat")}
                      title={t("askInChat")}
                    >
                      <MessageCircle className="h-3.5 w-3.5" />
                    </button>
                    {pendingDelete === memory.id ? (
                      <>
                        <button type="button" onClick={() => setPendingDelete(null)} className="btn-outline h-7 px-2 text-[10px]">{t("cancel")}</button>
                        <button type="button" onClick={() => void remove(memory.id)} className="btn-danger-outline h-7 px-2 text-[10px]">{t("delete")}</button>
                      </>
                    ) : (
                      <button
                        type="button"
                        onClick={() => setPendingDelete(memory.id)}
                        className="btn-ghost h-7 w-7 p-0 text-danger"
                        aria-label={t("deleteAria")}
                        title={t("deleteAria")}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                </article>
              ))}
            </div>
          ) : null}
        </div>
      </section>
    </div>
  );
}

function CategoryFilter({ label, value, active, onClick }: { label: string; value: number; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={classNames(
        "flex min-w-0 items-center justify-center gap-1 rounded-md px-2 py-2 text-[10.5px] font-bold transition md:w-full md:justify-start md:px-2.5",
        active ? "bg-white text-accent shadow-sm" : "text-ink-muted hover:bg-white",
      )}
    >
      <span className="truncate">{label}</span>
      <span className="text-[9.5px] font-semibold opacity-70 md:ml-auto">{value}</span>
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

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(currentLocale(), { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}
