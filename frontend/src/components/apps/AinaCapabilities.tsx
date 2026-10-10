import { useTranslation } from "react-i18next";
import i18n from "@/i18n";
import { ChevronDown } from "lucide-react";
import type {
  AinaCapabilityDefinition,
  AinaRecord,
  AinaUiCapabilityDefinition,
} from "@/types";

/** An AINA's declared skills, tools, UI and events, each as a list of expandable rows. */
export function AinaCapabilitySections({ record }: { record: AinaRecord }) {
  const { t } = useTranslation("aina");
  const { skills, tools, ui, events } = record.manifest.capabilities;
  return (
    <>
      <CapabilitySection title="Skills" count={skills.length} emptyText={t("skillsEmpty")}>
        {skills.map((skill) => <SkillDetails key={skill.id} skill={skill} />)}
      </CapabilitySection>
      <CapabilitySection title="Tools" count={tools.length} emptyText={t("toolsEmpty")}>
        {tools.map((tool) => <ToolDetails key={tool.id} tool={tool} />)}
      </CapabilitySection>
      {ui.length ? (
        <CapabilitySection title={t("uiTitle")} count={ui.length}>
          {ui.map((capability) => <UiDetails key={capability.id} capability={capability} />)}
        </CapabilitySection>
      ) : null}
      {events.length ? (
        <CapabilitySection title="Events" count={events.length}>
          <pre className="my-3 overflow-x-auto rounded-xl bg-slate-950 p-3 font-mono text-[10.5px] leading-relaxed text-slate-100">
            {JSON.stringify(events, null, 2)}
          </pre>
        </CapabilitySection>
      ) : null}
    </>
  );
}

function CapabilitySection({
  title,
  count,
  emptyText,
  children,
}: {
  title: string;
  count: number;
  emptyText?: string;
  children?: React.ReactNode;
}) {
  return (
    <section aria-label={title}>
      <h2 className="flex items-baseline gap-2 border-b border-line pb-3 text-[16px] font-semibold text-ink">
        {title}
        <span className="text-[13px] font-normal text-ink-subtle">{count}</span>
      </h2>
      {count ? <div className="divide-y divide-line">{children}</div> : <p className="py-4 text-[13px] text-ink-muted">{emptyText}</p>}
    </section>
  );
}

function SkillDetails({ skill }: { skill: AinaCapabilityDefinition }) {
  const { t } = useTranslation("aina");
  return (
    <CapabilityDisclosure capability={skill}>
      <Definition label={t("skillDesc")}>{skill.description}</Definition>
      <Definition label={t("skillPrompt")}>
        {skill.instructions ? (
          <pre className="whitespace-pre-wrap break-words rounded-lg bg-slate-950 p-3 font-mono text-[10.5px] leading-relaxed text-slate-100">
            {skill.instructions}
          </pre>
        ) : (
          <p className="text-[11.5px] text-ink-muted">{t("noPrompt")}</p>
        )}
      </Definition>
      <Definition label="Skill Input">
        <SchemaDetails schema={skill.input_schema} />
      </Definition>
    </CapabilityDisclosure>
  );
}

function ToolDetails({ tool }: { tool: AinaCapabilityDefinition }) {
  const { t } = useTranslation("aina");
  return (
    <CapabilityDisclosure capability={tool}>
      <Definition label={t("toolDesc")}>{tool.description}</Definition>
      {tool.instructions ? <Definition label={t("callNotes")}>{tool.instructions}</Definition> : null}
      <Definition label={t("inputParams")}>
        <SchemaDetails schema={tool.input_schema} />
      </Definition>
    </CapabilityDisclosure>
  );
}

function UiDetails({ capability }: { capability: AinaUiCapabilityDefinition }) {
  const { t } = useTranslation("aina");
  return (
    <CapabilityDisclosure capability={capability} badge={capability.kind}>
      <Definition label={t("uiDesc")}>{capability.description}</Definition>
      {capability.instructions ? <Definition label={t("renderNotes")}>{capability.instructions}</Definition> : null}
    </CapabilityDisclosure>
  );
}

function CapabilityDisclosure({
  capability,
  badge,
  children,
}: {
  capability: { id: string; name?: string; description?: string };
  badge?: string;
  children: React.ReactNode;
}) {
  return (
    <details className="group">
      <summary className="flex cursor-pointer list-none items-center gap-3 py-3 [&::-webkit-details-marker]:hidden">
        <div className="min-w-0 flex-1">
          <p className="text-[14px] font-medium text-ink">{capability.name ?? capability.id}</p>
          <p className="mt-0.5 truncate text-[13px] text-ink-muted">{capability.description || capability.id}</p>
        </div>
        {badge ? <span className="rounded-full bg-accent-soft px-2 py-0.5 font-mono text-[10px] text-accent">{badge}</span> : null}
        <ChevronDown className="h-4 w-4 shrink-0 text-ink-subtle transition-transform group-open:rotate-180" />
      </summary>
      <div className="mb-3 space-y-4 rounded-xl bg-app-soft px-4 py-4">
        <p className="font-mono text-[10.5px] text-ink-subtle">{capability.id}</p>
        {children}
      </div>
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
  const { t } = useTranslation("aina");
  const properties = isRecord(schema.properties) ? Object.entries(schema.properties) : [];
  const required = new Set(Array.isArray(schema.required) ? schema.required.filter((item): item is string => typeof item === "string") : []);

  return (
    <div className="space-y-2">
      {properties.length ? (
        <div className="overflow-x-auto rounded border border-line bg-white">
          <table className="w-full min-w-[520px] border-collapse text-left text-[10.5px]">
            <thead className="bg-app-soft text-ink-muted">
              <tr>
                <th className="border-b border-line px-2.5 py-2 font-bold">{t("param")}</th>
                <th className="border-b border-line px-2.5 py-2 font-bold">{t("type")}</th>
                <th className="border-b border-line px-2.5 py-2 font-bold">{t("requirement")}</th>
                <th className="border-b border-line px-2.5 py-2 font-bold">{t("description")}</th>
              </tr>
            </thead>
            <tbody className="[&>tr:last-child>td]:border-b-0">
              {properties.map(([name, rawDefinition]) => {
                const definition = isRecord(rawDefinition) ? rawDefinition : {};
                return (
                  <tr key={name}>
                    <td className="border-b border-line px-2.5 py-2 font-mono font-semibold text-ink">{name}</td>
                    <td className="border-b border-line px-2.5 py-2 font-mono text-accent">{schemaType(definition)}</td>
                    <td className="border-b border-line px-2.5 py-2 text-ink-muted">{required.has(name) ? t("required") : t("optional")}</td>
                    <td className="border-b border-line px-2.5 py-2 text-ink-muted">{schemaDescription(definition)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-[11px] text-ink-muted">{t("noFields")}</p>
      )}
      <details className="rounded border border-line bg-white">
        <summary className="cursor-pointer px-2.5 py-2 font-mono text-[9.5px] text-ink-muted">{t("viewSchema")}</summary>
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
  const choices = Array.isArray(definition.enum) ? i18n.t("aina:choices", { values: definition.enum.join(i18n.t("aina:choicesJoiner")) }) : "";
  return [description, choices].filter(Boolean).join(" ") || "—";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
