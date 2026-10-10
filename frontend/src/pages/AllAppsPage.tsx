import { useTranslation } from "react-i18next";
import i18n from "@/i18n";
import { type ChangeEvent, type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AppWindow,
  CheckCircle2,
  ChevronDown,
  Code2,
  Download,
  FileArchive,
  Info,
  Loader2,
  PackageCheck,
  Plus,
  RefreshCw,
  Rocket,
  Search,
  ShieldAlert,
  Trash2,
  Unplug,
  Upload,
  Wrench,
  X,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { ActionMenu, AppIcon, PluginChip, PluginRow, PluginSection, type MenuItem } from "@/components/apps/PluginUi";
import { Topbar } from "@/components/layout/Topbar";
import { api, apiErrorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ACTOR_QUERY, deleteAina, installAina, openAina, uninstallAina } from "@/lib/plugins";
import { classNames } from "@/lib/utils";
import type {
  AinaInstallation,
  AinaProjectRecord,
  AinaProjectScaffoldRequest,
  AinaRecord,
  SkillRecord,
  ToolRecord,
} from "@/types";

type View = "apps" | "developer";
type Kind = "aina" | "tools" | "skills";

const DEFAULT_PROJECT_SCAFFOLD: AinaProjectScaffoldRequest = {
  aina_id: "com.example.my-aina",
  name: "My AINA",
  description: "A managed AINA project.",
  version: "0.1.0",
  language: "python",
};

const SAMPLE_TOOL = {
  tool_id: "browser.demo.add",
  get name() { return i18n.t("apps:sample.tool.name"); },
  get description() { return i18n.t("apps:sample.tool.desc"); },
  input_schema: {
    type: "object",
    properties: { a: { type: "integer" }, b: { type: "integer" } },
    required: ["a", "b"],
    additionalProperties: false,
  },
  output_schema: {
    type: "object",
    properties: { result: { type: "integer" } },
    required: ["result"],
  },
  endpoint: "http://127.0.0.1:8099/tool/add",
  side_effect_level: "none",
  authentication: { type: "none" },
};

const SAMPLE_RISKY_TOOL = {
  ...SAMPLE_TOOL,
  tool_id: "browser.demo.risky-add",
  get name() { return i18n.t("apps:sample.risky.name"); },
  get description() { return i18n.t("apps:sample.risky.desc"); },
  side_effect_level: "high",
};

const SAMPLE_AINA = {
  protocol_version: "1.0",
  aina: {
    id: "com.example.browser-arithmetic",
    get name() { return i18n.t("apps:sample.aina.name"); },
    version: "1.0.0",
    get description() { return i18n.t("apps:sample.aina.desc"); },
    publisher: { id: "unibot-demo", name: "Unibot Demo" },
  },
  runtime: {
    type: "remote",
    endpoint: "http://127.0.0.1:8099/aina",
    streaming: false,
    async_tasks: false,
  },
  capabilities: {
    skills: [
      {
        id: "multiply",
        get name() { return i18n.t("apps:sample.skill1.name"); },
        get description() { return i18n.t("apps:sample.skill1.desc"); },
        input_schema: { type: "object" },
      },
    ],
    tools: [],
    ui: [],
    events: [],
  },
  main_widget: {
    id: "arithmetic-main",
    kind: "form",
    get title() { return i18n.t("apps:sample.widget.title"); },
    get description() { return i18n.t("apps:sample.widget.desc"); },
    get markdown() { return i18n.t("apps:sample.widget.markdown"); },
    fields: [
      { id: "left", get label() { return i18n.t("apps:sample.widget.left"); }, input_type: "number", placeholder: "6", required: true },
      { id: "right", get label() { return i18n.t("apps:sample.widget.right"); }, input_type: "number", placeholder: "7", required: true },
    ],
    actions: [
      {
        id: "multiply",
        get label() { return i18n.t("apps:sample.widget.action"); },
        kind: "prompt",
        get prompt() { return i18n.t("apps:sample.widget.prompt"); },
      },
    ],
  },
  permissions: [],
  authentication: { type: "none" },
};

const SAMPLE_SKILL = {
  skill_id: "browser.demo.arithmetic",
  get name() { return i18n.t("apps:sample.skill.name"); },
  get description() { return i18n.t("apps:sample.skill.desc"); },
  version: "1.0.0",
  input_schema: { type: "object" },
  output_schema: { type: "object" },
  get instructions() { return i18n.t("apps:sample.skill.instructions"); },
  tools: ["browser.demo.add"],
  permissions: [],
  publisher: "unibot-demo",
  visibility: "public",
  status: "published",
};

export default function AllAppsPage() {
  const { t } = useTranslation("apps");
  const navigate = useNavigate();
  const { user, config } = useAuth();
  const canManageRegistry = !config.auth_required || Boolean(user?.is_admin);
  const [view, setView] = useState<View>("apps");
  const [query, setQuery] = useState("");
  const [ainas, setAinas] = useState<AinaRecord[]>([]);
  const [projects, setProjects] = useState<AinaProjectRecord[]>([]);
  const [installations, setInstallations] = useState<AinaInstallation[]>([]);
  const [tools, setTools] = useState<ToolRecord[]>([]);
  const [skills, setSkills] = useState<SkillRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorKind, setEditorKind] = useState<Kind>("aina");
  const [editorText, setEditorText] = useState("");
  const [saving, setSaving] = useState(false);
  const [projectAction, setProjectAction] = useState<string | null>(null);
  const [scaffoldOpen, setScaffoldOpen] = useState(false);
  const [scaffold, setScaffold] = useState<AinaProjectScaffoldRequest>(DEFAULT_PROJECT_SCAFFOLD);
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const projectFileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ainaData, installationData, toolData, skillData, projectData] = await Promise.all([
        api.get<AinaRecord[]>("/ainas"),
        api.get<AinaInstallation[]>(`/installations?${ACTOR_QUERY}`),
        api.get<ToolRecord[]>("/tools"),
        api.get<SkillRecord[]>("/skills"),
        api.get<AinaProjectRecord[]>("/aina-projects").catch((projectError: unknown) => {
          setNotice({ tone: "error", text: apiErrorMessage(projectError) });
          return [];
        }),
      ]);
      setAinas(ainaData);
      setInstallations(installationData);
      setTools(toolData);
      setSkills(skillData);
      setProjects(projectData);
    } catch (loadError) {
      setNotice({ tone: "error", text: apiErrorMessage(loadError) });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const installedIds = useMemo(
    () => new Set(installations.filter((item) => item.status === "active").map((item) => item.aina_id)),
    [installations],
  );

  function openEditor(kind: Kind) {
    setView("developer");
    setEditorKind(kind);
    const sample = kind === "aina" ? SAMPLE_AINA : kind === "tools" ? SAMPLE_TOOL : SAMPLE_SKILL;
    setEditorText(JSON.stringify(sample, null, 2));
    setEditorOpen(true);
    setScaffoldOpen(false);
    setNotice(null);
  }

  async function importProject(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) return;

    setProjectAction("import");
    setNotice(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const project = await api.postForm<AinaProjectRecord>("/aina-projects", form);
      setProjects((current) => [project, ...current.filter((item) => item.id !== project.id)]);
      setNotice({
        tone: "success",
        text: t("notice.projectSaved", { name: project.manifest.aina.name }),
      });
    } catch (importError) {
      setNotice({ tone: "error", text: apiErrorMessage(importError) });
    } finally {
      setProjectAction(null);
    }
  }

  async function downloadScaffold(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setProjectAction("scaffold");
    setNotice(null);
    try {
      const result = await api.postBlob("/aina-projects/scaffold", scaffold);
      downloadBlob(
        result.blob,
        result.filename ?? `${scaffold.aina_id}-${scaffold.version ?? "0.1.0"}.aina.zip`,
      );
      setNotice({ tone: "success", text: t("notice.templateDownloaded") });
      setScaffoldOpen(false);
    } catch (scaffoldError) {
      setNotice({ tone: "error", text: apiErrorMessage(scaffoldError) });
    } finally {
      setProjectAction(null);
    }
  }

  async function downloadProject(project: AinaProjectRecord) {
    setProjectAction(project.id);
    setNotice(null);
    try {
      const result = await api.getBlob(
        `/aina-projects/${encodeURIComponent(project.id)}/archive`,
      );
      downloadBlob(result.blob, result.filename ?? project.source_filename);
    } catch (downloadError) {
      setNotice({ tone: "error", text: apiErrorMessage(downloadError) });
    } finally {
      setProjectAction(null);
    }
  }

  async function deleteProject(project: AinaProjectRecord) {
    if (!window.confirm(t("confirm.deleteProject", { name: project.manifest.aina.name }))) return;
    setProjectAction(project.id);
    setNotice(null);
    try {
      await api.delete(`/aina-projects/${encodeURIComponent(project.id)}`);
      setProjects((current) => current.filter((item) => item.id !== project.id));
      setNotice({ tone: "success", text: t("notice.projectDeleted", { name: project.manifest.aina.name }) });
    } catch (deleteError) {
      setNotice({ tone: "error", text: apiErrorMessage(deleteError) });
    } finally {
      setProjectAction(null);
    }
  }

  async function deployProject(project: AinaProjectRecord) {
    setProjectAction(project.id);
    setNotice(null);
    try {
      const deployed = await api.post<AinaProjectRecord>(
        `/aina-projects/${encodeURIComponent(project.id)}/deploy`,
      );
      setProjects((current) => current.map((item) => item.id === deployed.id ? deployed : item));
      setNotice({ tone: "success", text: t("notice.deployed", { name: deployed.manifest.aina.name }) });
      await load();
    } catch (deployError) {
      setNotice({ tone: "error", text: apiErrorMessage(deployError) });
    } finally {
      setProjectAction(null);
    }
  }

  async function undeployProject(project: AinaProjectRecord) {
    if (!window.confirm(t("confirm.undeploy", { name: project.manifest.aina.name }))) return;
    setProjectAction(project.id);
    setNotice(null);
    try {
      const deployed = await api.delete<AinaProjectRecord>(
        `/aina-projects/${encodeURIComponent(project.id)}/deployment`,
      );
      setProjects((current) => current.map((item) => item.id === deployed.id ? deployed : item));
      setNotice({ tone: "success", text: t("notice.undeployed", { name: deployed.manifest.aina.name }) });
      await load();
    } catch (deployError) {
      setNotice({ tone: "error", text: apiErrorMessage(deployError) });
    } finally {
      setProjectAction(null);
    }
  }

  async function registerDefinition() {
    setSaving(true);
    setNotice(null);
    try {
      const payload = JSON.parse(editorText) as unknown;
      const path = editorKind === "aina" ? "/ainas" : editorKind === "tools" ? "/tools" : "/skills";
      await api.post(path, payload);
      setNotice({ tone: "success", text: t("notice.registered", { label: kindLabel(editorKind) }) });
      setEditorOpen(false);
      await load();
    } catch (registerError) {
      setNotice({ tone: "error", text: apiErrorMessage(registerError) });
    } finally {
      setSaving(false);
    }
  }

  async function install(aina: AinaRecord) {
    try {
      await installAina(aina);
      setNotice({ tone: "success", text: t("notice.installed", { name: aina.manifest.aina.name }) });
      await load();
    } catch (installError) {
      setNotice({ tone: "error", text: apiErrorMessage(installError) });
    }
  }

  async function uninstall(aina: AinaRecord) {
    try {
      await uninstallAina(aina.manifest.aina.id);
      setNotice({ tone: "success", text: t("notice.uninstalled", { name: aina.manifest.aina.name }) });
      await load();
    } catch (uninstallError) {
      setNotice({ tone: "error", text: apiErrorMessage(uninstallError) });
    }
  }

  async function remove(kind: Kind, id: string) {
    try {
      if (kind === "aina") await deleteAina(id);
      else await api.delete(kind === "tools" ? `/tools/${id}` : `/skills/${id}`);
      setNotice({ tone: "success", text: t("notice.definitionDeleted") });
      await load();
    } catch (removeError) {
      setNotice({ tone: "error", text: apiErrorMessage(removeError) });
    }
  }

  async function open(aina: AinaRecord) {
    try {
      navigate(await openAina(aina.manifest.aina.id));
    } catch (openError) {
      setNotice({ tone: "error", text: apiErrorMessage(openError) });
    }
  }

  const matches = (...fields: string[]) => {
    const needle = query.trim().toLowerCase();
    return !needle || fields.some((field) => field.toLowerCase().includes(needle));
  };
  const visibleAinas = ainas.filter(({ manifest }) => matches(manifest.aina.name, manifest.aina.description, manifest.aina.id));
  const usable = (record: AinaRecord) => record.manifest.runtime.type === "builtin" || installedIds.has(record.manifest.aina.id);
  const visibleProjects = projects.filter(({ manifest }) => matches(manifest.aina.name, manifest.aina.description, manifest.aina.id));
  const visibleTools = tools.filter((tool) => matches(tool.name, tool.description, tool.tool_id));
  const visibleSkills = skills.filter((skill) => matches(skill.name, skill.description, skill.skill_id));

  const addItems: MenuItem[] = [
    ...(canManageRegistry
      ? (["aina", "tools", "skills"] as const).map((kind) => ({
        label: t("register", { label: kindLabel(kind) }),
        icon: <Plus className="h-4 w-4" />,
        onSelect: () => openEditor(kind),
      }))
      : []),
    {
      label: t("template"),
      icon: <FileArchive className="h-4 w-4" />,
      onSelect: () => {
        setView("developer");
        setScaffoldOpen(true);
        setEditorOpen(false);
        setNotice(null);
      },
    },
    {
      label: projectAction === "import" ? t("importing") : t("importZip"),
      icon: <Upload className="h-4 w-4" />,
      onSelect: () => {
        setView("developer");
        projectFileInput.current?.click();
      },
    },
  ];

  return (
    <div className="flex h-full flex-col bg-app-bg">
      <Topbar title={t("title")} />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl px-5 py-7 md:px-8 md:py-9">
          <header className="flex flex-wrap items-start gap-4">
            <div className="min-w-0 flex-1">
              <h2 className="text-[26px] font-semibold text-ink">{t("title")}</h2>
              <p className="mt-1 text-[14px] text-ink-muted">{t("subtitle")}</p>
            </div>
            <div className="flex w-full items-center gap-2 sm:w-auto">
              <label className="relative min-w-0 flex-1 sm:w-64 sm:flex-none">
                <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-subtle" />
                <input
                  type="search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={t("search")}
                  aria-label={t("search")}
                  className="h-10 w-full rounded-full border border-line bg-white pl-10 pr-4 text-[13.5px] text-ink outline-none transition placeholder:text-ink-subtle focus:border-accent"
                />
              </label>
              <button
                type="button"
                onClick={() => void load()}
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-ink-muted transition-colors hover:bg-app-soft hover:text-ink"
                aria-label={t("refreshAria")}
                title={t("refreshAria")}
              >
                <RefreshCw className="h-4 w-4" />
              </button>
              <ActionMenu
                label={t("add")}
                trigger={<>{t("add")}<ChevronDown className="h-4 w-4" /></>}
                triggerClassName="btn h-10 shrink-0 gap-1.5 rounded-full bg-ink px-4 text-white hover:bg-black"
                items={addItems}
                note={canManageRegistry ? undefined : t("adminOnly")}
              />
              <input
                ref={projectFileInput}
                type="file"
                accept=".zip,.aina.zip,application/zip"
                className="hidden"
                aria-label={t("zipAria")}
                onChange={(event) => void importProject(event)}
              />
            </div>
          </header>

          <div className="mt-6 flex items-center gap-1" role="tablist" aria-label={t("title")}>
            <PillTab active={view === "apps"} onClick={() => setView("apps")}>{t("tab.apps")}</PillTab>
            <PillTab active={view === "developer"} onClick={() => setView("developer")}>{t("tab.developer")}</PillTab>
          </div>

          <div className="mt-8 space-y-10">
            {notice ? <Notice {...notice} onClose={() => setNotice(null)} /> : null}
            {loading ? <LoadingRows /> : null}

            {!loading && view === "apps" ? (
              ainas.length ? (
                visibleAinas.length ? (
                  <>
                    {[
                      [t("section.installed"), visibleAinas.filter(usable)] as const,
                      [t("section.available"), visibleAinas.filter((record) => !usable(record))] as const,
                    ].map(([title, records]) => records.length ? (
                      <PluginSection key={title} title={title}>
                        <RowGrid>
                          {records.map((record) => (
                            <AinaRow
                              key={record.manifest.aina.id}
                              record={record}
                              installed={installedIds.has(record.manifest.aina.id)}
                              canDelete={canManageRegistry}
                              onInstall={() => void install(record)}
                              onUninstall={() => void uninstall(record)}
                              onOpen={() => void open(record)}
                              onDelete={() => void remove("aina", record.manifest.aina.id)}
                            />
                          ))}
                        </RowGrid>
                      </PluginSection>
                    ) : null)}
                  </>
                ) : <NoMatches query={query} />
              ) : <EmptyState title={t("aina.emptyTitle")} detail={t("aina.emptyDetail")} />
            ) : null}

            {!loading && view === "developer" ? (
              <>
                {editorOpen ? (
                  <DefinitionEditor
                    kind={editorKind}
                    text={editorText}
                    saving={saving}
                    onChange={setEditorText}
                    onLoadRiskySample={editorKind === "tools" ? () => setEditorText(JSON.stringify(SAMPLE_RISKY_TOOL, null, 2)) : undefined}
                    onClose={() => setEditorOpen(false)}
                    onSave={() => void registerDefinition()}
                  />
                ) : null}
                {scaffoldOpen ? (
                  <ProjectScaffoldForm
                    value={scaffold}
                    downloading={projectAction === "scaffold"}
                    onChange={setScaffold}
                    onCancel={() => setScaffoldOpen(false)}
                    onSubmit={(event) => void downloadScaffold(event)}
                  />
                ) : null}
                <PluginSection title="AINA Projects" count={projects.length}>
                  {visibleProjects.length ? (
                    <RowGrid>
                      {visibleProjects.map((project) => (
                        <ProjectRow
                          key={project.id}
                          project={project}
                          busy={projectAction === project.id}
                          disabled={projectAction !== null}
                          onDeploy={() => void deployProject(project)}
                          onUndeploy={() => void undeployProject(project)}
                          onDownload={() => void downloadProject(project)}
                          onDelete={() => void deleteProject(project)}
                        />
                      ))}
                    </RowGrid>
                  ) : <SectionEmpty text={projects.length ? t("noMatches", { query }) : t("projects.emptyBody")} />}
                </PluginSection>
                <PluginSection title={t("section.tools")} count={tools.length}>
                  {visibleTools.length ? (
                    <RowGrid>
                      {visibleTools.map((tool) => (
                        <PluginRow
                          key={tool.tool_id}
                          icon={<AppIcon id={tool.tool_id} name={tool.name} icon={tool.side_effect_level === "high" ? <ShieldAlert className="h-5 w-5 text-warning" /> : <Wrench className="h-5 w-5" />} />}
                          name={tool.name}
                          description={tool.description}
                          badge={tool.side_effect_level === "high" ? <PluginChip tone="warning">{t("tool.needsApproval")}</PluginChip> : null}
                          actions={canManageRegistry ? (
                            <ActionMenu
                              label={t("more", { name: tool.name })}
                              items={[{ label: t("delete"), icon: <Trash2 className="h-4 w-4" />, danger: true, onSelect: () => void remove("tools", tool.tool_id) }]}
                            />
                          ) : null}
                        />
                      ))}
                    </RowGrid>
                  ) : <SectionEmpty text={tools.length ? t("noMatches", { query }) : t("tool.emptyDetail")} />}
                </PluginSection>
                <PluginSection title={t("section.skills")} count={skills.length}>
                  {visibleSkills.length ? (
                    <RowGrid>
                      {visibleSkills.map((skill) => (
                        <PluginRow
                          key={skill.skill_id}
                          icon={<AppIcon id={skill.skill_id} name={skill.name} icon={<Code2 className="h-5 w-5" />} />}
                          name={skill.name}
                          description={skill.description}
                          badge={skill.status !== "published" ? <PluginChip>{skill.status}</PluginChip> : null}
                          actions={canManageRegistry ? (
                            <ActionMenu
                              label={t("more", { name: skill.name })}
                              items={[{ label: t("delete"), icon: <Trash2 className="h-4 w-4" />, danger: true, onSelect: () => void remove("skills", skill.skill_id) }]}
                            />
                          ) : null}
                        />
                      ))}
                    </RowGrid>
                  ) : <SectionEmpty text={skills.length ? t("noMatches", { query }) : t("skill.emptyDetail")} />}
                </PluginSection>
              </>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

function ProjectScaffoldForm({
  value,
  downloading,
  onChange,
  onCancel,
  onSubmit,
}: {
  value: AinaProjectScaffoldRequest;
  downloading: boolean;
  onChange: (value: AinaProjectScaffoldRequest) => void;
  onCancel: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  const { t } = useTranslation("apps");
  return (
    <form onSubmit={onSubmit} className="rounded-2xl border border-line bg-white p-4">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-app-soft text-ink-muted">
          <FileArchive className="h-5 w-5" />
        </div>
        <h2 className="text-[14px] font-semibold text-ink">{t("scaffold.title")}</h2>
        <InfoHint text={t("scaffold.desc")} />
      </div>
      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <ProjectField label="AINA ID">
          <input
            required
            value={value.aina_id}
            onChange={(event) => onChange({ ...value, aina_id: event.target.value })}
            className="input-soft"
            placeholder="com.example.my-aina"
          />
        </ProjectField>
        <ProjectField label={t("scaffold.name")}>
          <input
            required
            value={value.name}
            onChange={(event) => onChange({ ...value, name: event.target.value })}
            className="input-soft"
            placeholder="My AINA"
          />
        </ProjectField>
        <ProjectField label={t("scaffold.version")}>
          <input
            required
            value={value.version ?? ""}
            onChange={(event) => onChange({ ...value, version: event.target.value })}
            className="input-soft"
            placeholder="0.1.0"
          />
        </ProjectField>
        <ProjectField label={t("scaffold.language")}>
          <select
            value={value.language}
            onChange={(event) => onChange({ ...value, language: event.target.value as "python" | "node" })}
            className="input-soft"
          >
            <option value="python">Python</option>
            <option value="node">Node.js</option>
          </select>
        </ProjectField>
        <div className="sm:col-span-2">
          <ProjectField label={t("scaffold.description")}>
            <textarea
              required
              rows={3}
              value={value.description}
              onChange={(event) => onChange({ ...value, description: event.target.value })}
              className="input-soft resize-none"
              placeholder={t("scaffold.descPlaceholder")}
            />
          </ProjectField>
        </div>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="btn-outline rounded-full">{t("scaffold.cancel")}</button>
        <button type="submit" disabled={downloading} className="btn rounded-full bg-ink text-white hover:bg-black">
          {downloading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          {downloading ? t("scaffold.generating") : t("scaffold.download")}
        </button>
      </div>
    </form>
  );
}

function ProjectField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[11px] font-bold text-ink-muted">{label}</span>
      {children}
    </label>
  );
}

function AinaRow({ record, installed, canDelete, onInstall, onUninstall, onOpen, onDelete }: {
  record: AinaRecord;
  installed: boolean;
  canDelete: boolean;
  onInstall: () => void;
  onUninstall: () => void;
  onOpen: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation("apps");
  const { aina, runtime, permissions, main_widget } = record.manifest;
  const builtin = runtime.type === "builtin";
  const items: MenuItem[] = [];
  if (builtin || (installed && main_widget)) {
    items.push({ label: aina.id === "unibot-scheduler" ? t("aina.manageTasks") : t("aina.open"), icon: <AppWindow className="h-4 w-4" />, onSelect: onOpen });
  }
  if (installed && !builtin) items.push({ label: t("aina.uninstall"), icon: <Unplug className="h-4 w-4" />, onSelect: onUninstall });
  if (canDelete && !builtin && runtime.type !== "managed") {
    items.push({ label: t("delete"), icon: <Trash2 className="h-4 w-4" />, danger: true, onSelect: onDelete });
  }
  return (
    <PluginRow
      icon={<AppIcon id={aina.id} name={aina.name} />}
      name={aina.name}
      description={aina.description}
      to={`/plugin/${encodeURIComponent(aina.id)}`}
      // What installing grants, flagged while it is still a choice.
      badge={!builtin && !installed && permissions.length ? (
        <span title={permissions.join(", ")}><PluginChip tone="warning">{t("permissionCount", { count: permissions.length })}</PluginChip></span>
      ) : null}
      actions={builtin || installed ? (
        <ActionMenu label={t("more", { name: aina.name })} items={items} />
      ) : (
        <button
          type="button"
          onClick={onInstall}
          className="flex h-9 w-9 items-center justify-center rounded-full text-ink transition-colors hover:bg-white"
          aria-label={t("installNamed", { name: aina.name })}
          title={t("aina.install")}
        >
          <Plus className="h-5 w-5" />
        </button>
      )}
    />
  );
}

function ProjectRow({ project, busy, disabled, onDeploy, onUndeploy, onDownload, onDelete }: {
  project: AinaProjectRecord;
  busy: boolean;
  disabled: boolean;
  onDeploy: () => void;
  onUndeploy: () => void;
  onDownload: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation("apps");
  const { aina, runtime } = project.manifest;
  const managed = runtime.type === "managed";
  const items: MenuItem[] = [];
  if (project.status === "validated") items.push({ label: t("project.deploy"), icon: <Rocket className="h-4 w-4" />, onSelect: onDeploy });
  if (project.status === "deployed") items.push({ label: t("project.undeploy"), icon: <Unplug className="h-4 w-4" />, onSelect: onUndeploy });
  if (project.status !== "importing") items.push({ label: t("project.download"), icon: <Download className="h-4 w-4" />, onSelect: onDownload });
  if (project.status !== "deployed") items.push({ label: t("delete"), icon: <Trash2 className="h-4 w-4" />, danger: true, onSelect: onDelete });
  const status = project.status === "deployed"
    ? t("project.deployed")
    : project.status === "validated"
      ? managed ? t("project.validatedManaged") : t("project.validated")
      : t("project.incomplete");
  return (
    <PluginRow
      icon={<AppIcon id={aina.id} name={aina.name} icon={<PackageCheck className="h-5 w-5" />} />}
      name={aina.name}
      description={`${aina.id} · v${aina.version} · ${t("project.files", { filename: project.source_filename, count: project.file_count, size: formatBytes(project.size_bytes) })}`}
      badge={<PluginChip tone={project.status === "deployed" || project.status === "validated" ? "success" : "warning"}>{status}</PluginChip>}
      actions={busy ? (
        <span className="flex h-9 w-9 items-center justify-center text-ink-muted"><Loader2 className="h-4 w-4 animate-spin" /></span>
      ) : disabled ? null : (
        <ActionMenu label={t("more", { name: aina.name })} items={items} />
      )}
    />
  );
}

function RowGrid({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-1 gap-x-10 gap-y-1 md:grid-cols-2">{children}</div>;
}

function PillTab({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={classNames(
        "h-9 rounded-full px-4 text-[13.5px] transition-colors",
        active ? "bg-sidebar-active font-medium text-ink" : "text-ink-muted hover:bg-app-soft hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}

function DefinitionEditor({ kind, text, saving, onChange, onLoadRiskySample, onClose, onSave }: {
  kind: Kind;
  text: string;
  saving: boolean;
  onChange: (text: string) => void;
  onLoadRiskySample?: () => void;
  onClose: () => void;
  onSave: () => void;
}) {
  const { t } = useTranslation("apps");
  return (
    <section className="overflow-hidden rounded-2xl border border-line bg-white">
      <div className="flex h-12 items-center gap-2 border-b border-line px-4">
        <h2 className="text-[14px] font-semibold text-ink">{t("editor.title", { label: kindLabel(kind) })}</h2>
        <InfoHint text={t("editor.hint")} />
        <span className="flex-1" />
        <button type="button" onClick={onClose} className="btn-ghost h-8" aria-label={t("editor.close")}><X className="w-4 h-4" /></button>
      </div>
      <div className="p-4">
        <textarea
          value={text}
          onChange={(event) => onChange(event.target.value)}
          rows={18}
          spellCheck={false}
          aria-label={`${kindLabel(kind)} JSON`}
          className="w-full rounded-xl border border-line-strong bg-slate-950 p-3 font-mono text-[11.5px] leading-relaxed text-slate-100 outline-none focus:border-accent"
        />
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {onLoadRiskySample ? (
            <button type="button" onClick={onLoadRiskySample} className="inline-flex items-center gap-1.5 whitespace-nowrap text-[12px] text-warning-deep hover:underline">
              <ShieldAlert className="h-3.5 w-3.5" />{t("loadRiskySample")}
            </button>
          ) : null}
          <div className="ml-auto flex gap-2 whitespace-nowrap">
            <button type="button" onClick={onClose} className="btn-outline rounded-full">{t("editor.cancel")}</button>
            <button type="button" disabled={saving} onClick={onSave} className="btn rounded-full bg-ink text-white hover:bg-black">{saving ? t("editor.registering") : t("editor.submit")}</button>
          </div>
        </div>
      </div>
    </section>
  );
}

// Explanatory copy sits behind an icon instead of a paragraph: hover shows it, screen readers read it.
function InfoHint({ text }: { text: string }) {
  return (
    <span role="img" aria-label={text} title={text} className="inline-flex cursor-help text-ink-subtle hover:text-ink-muted">
      <Info className="h-3.5 w-3.5" />
    </span>
  );
}

function SectionEmpty({ text }: { text: string }) {
  return <p className="px-2 py-3 text-[13px] text-ink-subtle">{text}</p>;
}

function NoMatches({ query }: { query: string }) {
  const { t } = useTranslation("apps");
  return <p className="py-16 text-center text-[14px] text-ink-muted">{t("noMatches", { query })}</p>;
}

function Notice({ tone, text, onClose }: { tone: "success" | "error"; text: string; onClose: () => void }) {
  const { t } = useTranslation("apps");
  return (
    <div className={classNames("flex items-center gap-2.5 rounded-xl px-3.5 py-2.5", tone === "success" ? "bg-success-soft text-success-deep" : "bg-danger-soft text-danger-deep")}>
      {tone === "success" ? <CheckCircle2 className="w-4 h-4" /> : <ShieldAlert className="w-4 h-4" />}
      <span className="flex-1 text-[13px] font-medium">{text}</span>
      <button type="button" onClick={onClose} aria-label={t("dismiss")}><X className="w-4 h-4" /></button>
    </div>
  );
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="py-20 text-center">
      <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
      <p className="mt-1 text-[13px] text-ink-muted">{detail}</p>
    </div>
  );
}

function LoadingRows() {
  return (
    <div className="grid grid-cols-1 gap-x-10 gap-y-3 md:grid-cols-2">
      {[0, 1, 2, 3].map((item) => <div key={item} className="h-14 animate-pulse rounded-xl bg-line/60" />)}
    </div>
  );
}

function kindLabel(kind: Kind): string {
  return kind === "aina" ? "AINA" : i18n.t(`apps:label.${kind}`);
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
