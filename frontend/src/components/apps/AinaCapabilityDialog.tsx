import { useEffect } from "react";
import {
  AppWindow,
  BookOpenText,
  Braces,
  ChevronDown,
  Monitor,
  Wrench,
  X,
} from "lucide-react";
import type {
  AinaCapabilityDefinition,
  AinaRecord,
  AinaUiCapabilityDefinition,
} from "@/types";

export function AinaCapabilityDialog({
  record,
  onClose,
}: {
  record: AinaRecord;
  onClose: () => void;
}) {
  const { manifest } = record;
  const { skills, tools, ui, events } = manifest.capabilities;
  const titleId = `${manifest.aina.id}-capability-title`;

  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="flex h-[min(760px,calc(100vh-32px))] w-full max-w-5xl flex-col overflow-hidden rounded-lg border border-line bg-white shadow-soft"
      >
        <header className="flex h-16 shrink-0 items-center gap-3 border-b border-line px-5">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent-soft text-accent">
            <AppWindow className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="truncate text-[15px] font-extrabold text-ink">
              {manifest.aina.name} 能力详情
            </h2>
            <p className="truncate font-mono text-[10.5px] text-ink-muted">
              {manifest.aina.id} · v{manifest.aina.version}
            </p>
          </div>
          <button type="button" onClick={onClose} className="btn-ghost h-8 w-8 p-0" aria-label="关闭能力详情">
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-[240px_minmax(0,1fr)] max-md:grid-cols-1">
          <aside className="overflow-y-auto border-r border-line bg-app-soft p-4 max-md:max-h-52 max-md:border-b max-md:border-r-0">
            <p className="text-[12px] leading-relaxed text-ink-muted">{manifest.aina.description}</p>
            <dl className="mt-4 space-y-2 text-[11px]">
              <MetadataRow label="运行方式" value={manifest.runtime.type === "builtin" ? "系统内置" : "远程服务"} />
              <MetadataRow label="发布者" value={manifest.aina.publisher.name} />
              <MetadataRow label="Skill" value={String(skills.length)} />
              <MetadataRow label="Tool" value={String(tools.length)} />
              <MetadataRow label="UI" value={String(ui.length)} />
              <MetadataRow label="Event" value={String(events.length)} />
            </dl>
            {manifest.permissions.length ? (
              <div className="mt-5 border-t border-line pt-4">
                <h3 className="text-[10.5px] font-bold text-ink">所需权限</h3>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {manifest.permissions.map((permission) => (
                    <span key={permission} className="rounded bg-warning-soft px-1.5 py-1 font-mono text-[9.5px] text-warning-deep">
                      {permission}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
          </aside>

          <main className="min-w-0 overflow-y-auto p-5">
            <div className="space-y-5">
              <CapabilitySection
                title="Skills"
                count={skills.length}
                icon={<BookOpenText className="h-4 w-4" />}
                emptyText="该 AINA 没有声明 Skill。"
              >
                {skills.map((skill, index) => (
                  <SkillDetails key={skill.id} skill={skill} defaultOpen={index === 0} />
                ))}
              </CapabilitySection>

              <CapabilitySection
                title="Tools"
                count={tools.length}
                icon={<Wrench className="h-4 w-4" />}
                emptyText="该 AINA 没有声明 Tool。"
              >
                {tools.map((tool, index) => (
                  <ToolDetails key={tool.id} tool={tool} defaultOpen={index === 0} />
                ))}
              </CapabilitySection>

              <CapabilitySection
                title="UI 能力"
                count={ui.length}
                icon={<Monitor className="h-4 w-4" />}
                emptyText="该 AINA 没有声明 UI 能力。"
              >
                {ui.map((capability, index) => (
                  <UiDetails key={capability.id} capability={capability} defaultOpen={index === 0} />
                ))}
              </CapabilitySection>

              {events.length ? (
                <CapabilitySection title="Events" count={events.length} icon={<Braces className="h-4 w-4" />}>
                  <pre className="overflow-x-auto bg-slate-950 p-3 font-mono text-[10.5px] leading-relaxed text-slate-100">
                    {JSON.stringify(events, null, 2)}
                  </pre>
                </CapabilitySection>
              ) : null}
            </div>
          </main>
        </div>
      </section>
    </div>
  );
}

function MetadataRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-3">
      <dt className="text-ink-muted">{label}</dt>
      <dd className="ml-auto font-semibold text-ink">{value}</dd>
    </div>
  );
}

function CapabilitySection({
  title,
  count,
  icon,
  emptyText,
  children,
}: {
  title: string;
  count: number;
  icon: React.ReactNode;
  emptyText?: string;
  children?: React.ReactNode;
}) {
  return (
    <section aria-label={title}>
      <div className="mb-2 flex items-center gap-2 text-ink">
        <span className="text-accent">{icon}</span>
        <h3 className="text-[12.5px] font-extrabold">{title}</h3>
        <span className="rounded bg-app-soft px-1.5 py-0.5 text-[9.5px] font-bold text-ink-muted">{count}</span>
      </div>
      <div className="overflow-hidden rounded-lg border border-line bg-white">
        {count ? children : <p className="px-3 py-4 text-[11.5px] text-ink-muted">{emptyText}</p>}
      </div>
    </section>
  );
}

function SkillDetails({ skill, defaultOpen }: { skill: AinaCapabilityDefinition; defaultOpen: boolean }) {
  return (
    <CapabilityDisclosure capability={skill} defaultOpen={defaultOpen}>
      <Definition label="Skill 描述">{skill.description}</Definition>
      <Definition label="Skill 提示词">
        {skill.instructions ? (
          <pre className="whitespace-pre-wrap break-words bg-slate-950 p-3 font-mono text-[10.5px] leading-relaxed text-slate-100">
            {skill.instructions}
          </pre>
        ) : (
          <p className="text-[11.5px] text-ink-muted">未提供提示词。</p>
        )}
      </Definition>
      <Definition label="Skill Input">
        <SchemaDetails schema={skill.input_schema} />
      </Definition>
    </CapabilityDisclosure>
  );
}

function ToolDetails({ tool, defaultOpen }: { tool: AinaCapabilityDefinition; defaultOpen: boolean }) {
  return (
    <CapabilityDisclosure capability={tool} defaultOpen={defaultOpen}>
      <Definition label="Tool 描述">{tool.description}</Definition>
      {tool.instructions ? <Definition label="调用说明">{tool.instructions}</Definition> : null}
      <Definition label="Input 参数">
        <SchemaDetails schema={tool.input_schema} />
      </Definition>
    </CapabilityDisclosure>
  );
}

function UiDetails({
  capability,
  defaultOpen,
}: {
  capability: AinaUiCapabilityDefinition;
  defaultOpen: boolean;
}) {
  return (
    <CapabilityDisclosure capability={capability} defaultOpen={defaultOpen} badge={capability.kind}>
      <Definition label="UI 描述">{capability.description}</Definition>
      {capability.instructions ? <Definition label="渲染说明">{capability.instructions}</Definition> : null}
    </CapabilityDisclosure>
  );
}

function CapabilityDisclosure({
  capability,
  badge,
  defaultOpen,
  children,
}: {
  capability: { id: string; name?: string };
  badge?: string;
  defaultOpen: boolean;
  children: React.ReactNode;
}) {
  return (
    <details className="group border-b border-line last:border-b-0" open={defaultOpen || undefined}>
      <summary className="flex cursor-pointer list-none items-center gap-3 px-3 py-2.5 hover:bg-app-soft [&::-webkit-details-marker]:hidden">
        <div className="min-w-0 flex-1">
          <p className="text-[12px] font-bold text-ink">{capability.name ?? capability.id}</p>
          <p className="truncate font-mono text-[9.5px] text-ink-muted">{capability.id}</p>
        </div>
        {badge ? <span className="rounded bg-accent-soft px-1.5 py-0.5 font-mono text-[9px] text-accent">{badge}</span> : null}
        <ChevronDown className="h-3.5 w-3.5 text-ink-muted transition-transform group-open:rotate-180" />
      </summary>
      <div className="space-y-4 border-t border-line bg-app-soft px-4 py-4">{children}</div>
    </details>
  );
}

function Definition({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="mb-1.5 text-[10.5px] font-bold text-ink">{label}</h4>
      <div className="text-[11.5px] leading-relaxed text-ink-muted">{children}</div>
    </div>
  );
}

function SchemaDetails({ schema }: { schema: Record<string, unknown> }) {
  const properties = isRecord(schema.properties) ? Object.entries(schema.properties) : [];
  const required = new Set(Array.isArray(schema.required) ? schema.required.filter((item): item is string => typeof item === "string") : []);

  return (
    <div className="space-y-2">
      {properties.length ? (
        <div className="overflow-x-auto rounded border border-line bg-white">
          <table className="w-full min-w-[520px] border-collapse text-left text-[10.5px]">
            <thead className="bg-app-soft text-ink-muted">
              <tr>
                <th className="border-b border-line px-2.5 py-2 font-bold">参数</th>
                <th className="border-b border-line px-2.5 py-2 font-bold">类型</th>
                <th className="border-b border-line px-2.5 py-2 font-bold">要求</th>
                <th className="border-b border-line px-2.5 py-2 font-bold">说明</th>
              </tr>
            </thead>
            <tbody className="[&>tr:last-child>td]:border-b-0">
              {properties.map(([name, rawDefinition]) => {
                const definition = isRecord(rawDefinition) ? rawDefinition : {};
                return (
                  <tr key={name}>
                    <td className="border-b border-line px-2.5 py-2 font-mono font-semibold text-ink">{name}</td>
                    <td className="border-b border-line px-2.5 py-2 font-mono text-accent">{schemaType(definition)}</td>
                    <td className="border-b border-line px-2.5 py-2 text-ink-muted">{required.has(name) ? "必填" : "可选"}</td>
                    <td className="border-b border-line px-2.5 py-2 text-ink-muted">{schemaDescription(definition)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-[11px] text-ink-muted">没有声明输入字段。</p>
      )}
      <details className="rounded border border-line bg-white">
        <summary className="cursor-pointer px-2.5 py-2 font-mono text-[9.5px] text-ink-muted">查看 JSON Schema</summary>
        <pre className="overflow-x-auto border-t border-line bg-slate-950 p-3 font-mono text-[10px] leading-relaxed text-slate-100">
          {JSON.stringify(schema, null, 2)}
        </pre>
      </details>
    </div>
  );
}

function schemaType(definition: Record<string, unknown>): string {
  const type = typeof definition.type === "string" ? definition.type : "unknown";
  if (type === "array" && isRecord(definition.items)) return `array<${schemaType(definition.items)}>`;
  return type;
}

function schemaDescription(definition: Record<string, unknown>): string {
  const description = typeof definition.description === "string" ? definition.description : "";
  const choices = Array.isArray(definition.enum) ? `可选值：${definition.enum.join("、")}` : "";
  return [description, choices].filter(Boolean).join(" ") || "—";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
