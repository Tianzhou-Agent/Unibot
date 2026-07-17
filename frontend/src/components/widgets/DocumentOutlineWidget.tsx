import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, FileText, ListTree, RefreshCw } from "lucide-react";
import { MarkdownContent } from "@/components/chat/MarkdownContent";
import { api, apiErrorMessage } from "@/lib/api";
import { classNames } from "@/lib/utils";
import type {
  DocumentSectionRecord,
  WidgetDefinition,
  WidgetDocumentSectionDefinition,
} from "@/types";

export function DocumentOutlineWidget({ widget }: { widget: WidgetDefinition }) {
  const sections = useMemo(() => widget.sections ?? [], [widget.sections]);
  const defaultSection = useMemo(() => chooseDefaultSection(sections), [sections]);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(defaultSection?.index ?? null);
  const [section, setSection] = useState<DocumentSectionRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    setSelectedIndex(defaultSection?.index ?? null);
  }, [defaultSection?.index, widget.id]);

  const selected = sections.find((item) => item.index === selectedIndex) ?? defaultSection;
  const rootLevel = sections.length ? Math.min(...sections.map((item) => item.level)) : 1;
  const chapterLevel = sections.some((item) => item.level === rootLevel + 1) ? rootLevel + 1 : rootLevel;
  const chapterCount = sections.filter((item) => item.level === chapterLevel).length;

  useEffect(() => {
    const documentName = widget.document_name;
    if (!documentName || !selected) {
      setSection(null);
      return;
    }
    let active = true;
    setLoading(true);
    setError(null);
    const query = new URLSearchParams({
      heading: selected.heading,
      occurrence: String(selected.occurrence),
      user_id: "anonymous",
      tenant_id: "default",
    });
    void api
      .get<DocumentSectionRecord>(`/documents/${encodeURIComponent(documentName)}/sections?${query}`)
      .then((result) => {
        if (active) setSection(result);
      })
      .catch((loadError) => {
        if (active) {
          setSection(null);
          setError(apiErrorMessage(loadError));
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [reloadKey, selected, widget.document_name]);

  return (
    <section className="overflow-hidden rounded-lg border border-line bg-white shadow-soft" aria-label={`文档章节 ${widget.title}`}>
      <header className="flex min-h-14 items-center gap-3 border-b border-line bg-app-soft px-4 py-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white text-accent shadow-sm">
          <FileText className="h-4.5 w-4.5" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-[13.5px] font-extrabold text-ink">{widget.title}</h3>
          <p className="mt-0.5 text-[10.5px] text-ink-muted">{chapterCount} 个章节 · {sections.length} 个标题</p>
        </div>
        <span className="rounded bg-accent-soft px-2 py-1 text-[9.5px] font-bold text-accent">章节浏览</span>
      </header>

      <div className="grid h-[min(560px,68vh)] min-h-[400px] grid-rows-[180px_minmax(0,1fr)] md:grid-cols-[220px_minmax(0,1fr)] md:grid-rows-1">
        <aside className="flex min-h-0 flex-col border-b border-line bg-app-soft md:border-b-0 md:border-r">
          <div className="flex h-10 shrink-0 items-center gap-2 border-b border-line px-3 text-[10.5px] font-bold text-ink-muted">
            <ListTree className="h-3.5 w-3.5" />选择章节
          </div>
          <nav className="min-h-0 flex-1 overflow-y-auto p-1.5" aria-label="文档章节目录">
            {sections.map((item) => {
              const active = item.index === selected?.index;
              const depth = Math.max(0, item.level - rootLevel);
              return (
                <button
                  key={`${item.index}-${item.occurrence}`}
                  type="button"
                  onClick={() => setSelectedIndex(item.index)}
                  className={classNames(
                    "mb-0.5 block min-h-8 w-full rounded-md py-1.5 pr-2 text-left text-[10.5px] leading-4 transition-colors",
                    active ? "bg-accent text-white" : "text-ink-muted hover:bg-white hover:text-ink",
                    item.level <= chapterLevel && "font-bold",
                  )}
                  style={{ paddingLeft: `${8 + Math.min(depth, 3) * 14}px` }}
                  aria-current={active ? "page" : undefined}
                >
                  <span className="line-clamp-2">{item.heading}</span>
                </button>
              );
            })}
          </nav>
        </aside>

        <div className="flex min-h-0 min-w-0 flex-col bg-white">
          {selected ? (
            <div className="flex h-10 shrink-0 items-center border-b border-line px-3 text-[10px] text-ink-subtle">
              <span className="truncate">{selected.heading}</span>
              <span className="ml-auto shrink-0 pl-3">第 {selected.line_start}-{selected.line_end} 行</span>
            </div>
          ) : null}
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            {loading ? <SectionLoading /> : null}
            {!loading && error ? (
              <div className="flex min-h-40 flex-col items-center justify-center text-center">
                <AlertTriangle className="h-6 w-6 text-warning" />
                <p className="mt-2 text-[11.5px] text-danger-deep">{error}</p>
                <button type="button" onClick={() => setReloadKey((value) => value + 1)} className="btn-outline mt-3 h-8 text-[11px]">
                  <RefreshCw className="h-3.5 w-3.5" />重试
                </button>
              </div>
            ) : null}
            {!loading && !error && section ? <MarkdownContent content={section.content} /> : null}
            {!loading && !error && !section ? <p className="py-16 text-center text-[11.5px] text-ink-muted">当前文档没有可浏览的章节。</p> : null}
          </div>
        </div>
      </div>
    </section>
  );
}

function chooseDefaultSection(
  sections: WidgetDocumentSectionDefinition[],
): WidgetDocumentSectionDefinition | null {
  if (!sections.length) return null;
  const rootLevel = Math.min(...sections.map((item) => item.level));
  return sections.find((item) => item.level === rootLevel + 1) ?? sections[0];
}

function SectionLoading() {
  return (
    <div className="animate-pulse space-y-3" aria-label="正在加载章节">
      <div className="h-6 w-2/3 rounded bg-line" />
      <div className="h-3 w-full rounded bg-line/80" />
      <div className="h-3 w-11/12 rounded bg-line/80" />
      <div className="h-3 w-4/5 rounded bg-line/80" />
    </div>
  );
}
