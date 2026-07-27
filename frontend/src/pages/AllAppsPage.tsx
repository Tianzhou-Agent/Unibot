import { type ChangeEvent, type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AppWindow,
  Box,
  CheckCircle2,
  Code2,
  Download,
  ExternalLink,
  FileArchive,
  ListTree,
  Loader2,
  PackageCheck,
  Plus,
  RefreshCw,
  Rocket,
  ShieldAlert,
  Trash2,
  Unplug,
  Upload,
  Wrench,
  X,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { AinaCapabilityDialog } from "@/components/apps/AinaCapabilityDialog";
import { Topbar } from "@/components/layout/Topbar";
import { api, apiErrorMessage } from "@/lib/api";
import { classNames } from "@/lib/utils";
import type {
  AinaCanvasResponse,
  AinaInstallation,
  AinaProjectRecord,
  AinaProjectScaffoldRequest,
  AinaRecord,
  SkillRecord,
  ToolRecord,
} from "@/types";

type Tab = "aina" | "tools" | "skills";

const ACTOR_QUERY = "user_id=anonymous&tenant_id=default";

const DEFAULT_PROJECT_SCAFFOLD: AinaProjectScaffoldRequest = {
  aina_id: "com.example.my-aina",
  name: "My AINA",
  description: "A managed AINA project.",
  version: "0.1.0",
  language: "python",
};

const SAMPLE_TOOL = {
  tool_id: "browser.demo.add",
  name: "浏览器加法工具",
  description: "通过本地演示运行服务将两个整数相加。",
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
  name: "高风险演示工具",
  description: "用于验证授权门禁的演示调用；实际只执行整数相加。",
  side_effect_level: "high",
};

const SAMPLE_AINA = {
  protocol_version: "1.0",
  aina: {
    id: "com.example.browser-arithmetic",
    name: "浏览器算术 AINA",
    version: "1.0.0",
    description: "用于浏览器端到端测试的远程算术应用。",
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
        name: "整数乘法",
        description: "返回确定性的整数乘法结果。",
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
    title: "整数乘法",
    description: "输入两个整数，通过对话调用当前 AINA。",
    markdown: "### 交互式乘法\n\n你也可以在左侧对话框直接描述计算需求。",
    fields: [
      { id: "left", label: "第一个整数", input_type: "number", placeholder: "6", required: true },
      { id: "right", label: "第二个整数", input_type: "number", placeholder: "7", required: true },
    ],
    actions: [
      {
        id: "multiply",
        label: "计算乘积",
        kind: "prompt",
        prompt: "请计算 {left} 乘以 {right}，并返回结果。",
      },
    ],
  },
  permissions: [],
  authentication: { type: "none" },
};

const SAMPLE_SKILL = {
  skill_id: "browser.demo.arithmetic",
  name: "算术验证技能",
  description: "指导智能体优先使用已注册的确定性算术工具。",
  version: "1.0.0",
  input_schema: { type: "object" },
  output_schema: { type: "object" },
  instructions: "遇到精确加法请求时，调用浏览器加法工具，并基于工具结果回答。",
  tools: ["browser.demo.add"],
  permissions: [],
  publisher: "unibot-demo",
  visibility: "public",
  status: "published",
};

export default function AllAppsPage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("aina");
  const [ainas, setAinas] = useState<AinaRecord[]>([]);
  const [projects, setProjects] = useState<AinaProjectRecord[]>([]);
  const [installations, setInstallations] = useState<AinaInstallation[]>([]);
  const [tools, setTools] = useState<ToolRecord[]>([]);
  const [skills, setSkills] = useState<SkillRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [editorOpen, setEditorOpen] = useState(false);
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

  function openEditor(targetTab: Tab = tab, preset?: "default" | "risky") {
    setTab(targetTab);
    const sample =
      targetTab === "aina"
        ? SAMPLE_AINA
        : targetTab === "tools"
          ? preset === "risky"
            ? SAMPLE_RISKY_TOOL
            : SAMPLE_TOOL
          : SAMPLE_SKILL;
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
        text: `${project.manifest.aina.name} 已校验并保存为项目，尚未部署。`,
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
      setNotice({ tone: "success", text: "AINA Project 模板已下载。" });
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
    if (!window.confirm(`删除项目“${project.manifest.aina.name}”？此操作不会影响已注册的 AINA。`)) return;
    setProjectAction(project.id);
    setNotice(null);
    try {
      await api.delete(`/aina-projects/${encodeURIComponent(project.id)}`);
      setProjects((current) => current.filter((item) => item.id !== project.id));
      setNotice({ tone: "success", text: `${project.manifest.aina.name} 项目已删除。` });
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
      setNotice({ tone: "success", text: `${deployed.manifest.aina.name} 已部署，可以安装并从对话调用。` });
      await load();
    } catch (deployError) {
      setNotice({ tone: "error", text: apiErrorMessage(deployError) });
    } finally {
      setProjectAction(null);
    }
  }

  async function undeployProject(project: AinaProjectRecord) {
    if (!window.confirm(`取消部署“${project.manifest.aina.name}”？已有安装也会一并移除。`)) return;
    setProjectAction(project.id);
    setNotice(null);
    try {
      const deployed = await api.delete<AinaProjectRecord>(
        `/aina-projects/${encodeURIComponent(project.id)}/deployment`,
      );
      setProjects((current) => current.map((item) => item.id === deployed.id ? deployed : item));
      setNotice({ tone: "success", text: `${deployed.manifest.aina.name} 已取消部署，源码项目仍然保留。` });
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
      const path = tab === "aina" ? "/ainas" : tab === "tools" ? "/tools" : "/skills";
      await api.post(path, payload);
      setNotice({ tone: "success", text: `${tabLabel(tab)}注册成功。` });
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
      await api.post(`/ainas/${aina.manifest.aina.id}/install`, {
        user_id: "anonymous",
        tenant_id: "default",
        granted_permissions: aina.manifest.permissions,
        configuration: {},
      });
      setNotice({ tone: "success", text: `${aina.manifest.aina.name} 已安装并完成权限授权。` });
      await load();
    } catch (installError) {
      setNotice({ tone: "error", text: apiErrorMessage(installError) });
    }
  }

  async function uninstall(aina: AinaRecord) {
    try {
      await api.delete(
        `/ainas/${aina.manifest.aina.id}/install?user_id=anonymous&tenant_id=default`,
      );
      setNotice({ tone: "success", text: `${aina.manifest.aina.name} 已卸载。` });
      await load();
    } catch (uninstallError) {
      setNotice({ tone: "error", text: apiErrorMessage(uninstallError) });
    }
  }

  async function remove(kind: Tab, id: string) {
    const path = kind === "aina" ? `/ainas/${id}` : kind === "tools" ? `/tools/${id}` : `/skills/${id}`;
    try {
      await api.delete(path);
      setNotice({ tone: "success", text: "能力定义已删除。" });
      await load();
    } catch (removeError) {
      setNotice({ tone: "error", text: apiErrorMessage(removeError) });
    }
  }

  async function open(aina: AinaRecord) {
    try {
      const canvas = await api.post<AinaCanvasResponse>(`/ainas/${aina.manifest.aina.id}/open`, {
        user_id: "anonymous",
        tenant_id: "default",
      });
      navigate(canvas.route, { state: { canvas } });
    } catch (openError) {
      setNotice({ tone: "error", text: apiErrorMessage(openError) });
    }
  }

  const total = ainas.length + tools.length + skills.length;
  return (
    <div className="h-full flex flex-col bg-app-bg">
      <Topbar
        title="能力中心"
        badge={{ label: `${total} 项能力`, tone: "neutral" }}
        actions={
          <button type="button" onClick={() => void load()} className="btn-outline h-8" aria-label="刷新能力">
            <RefreshCw className="w-3.5 h-3.5" />刷新
          </button>
        }
      />
      <div className="flex-1 min-h-0 overflow-y-auto p-4">
        <div className="mx-auto max-w-6xl space-y-4">
          <section className="rounded-xl border border-line bg-white p-4 shadow-card">
            <div className="flex flex-wrap items-center gap-2">
              <TabButton active={tab === "aina"} onClick={() => setTab("aina")} icon={<AppWindow className="w-4 h-4" />}>
                AINA 应用 <Count value={ainas.length} />
              </TabButton>
              <TabButton active={tab === "tools"} onClick={() => setTab("tools")} icon={<Wrench className="w-4 h-4" />}>
                工具 <Count value={tools.length} />
              </TabButton>
              <TabButton active={tab === "skills"} onClick={() => setTab("skills")} icon={<Code2 className="w-4 h-4" />}>
                技能 <Count value={skills.length} />
              </TabButton>
              <span className="flex-1" />
              {tab === "tools" ? (
                <button type="button" onClick={() => openEditor("tools", "risky")} className="btn-outline">
                  <ShieldAlert className="w-4 h-4 text-warning" />高风险示例
                </button>
              ) : null}
              {tab === "aina" ? (
                <>
                  <input
                    ref={projectFileInput}
                    type="file"
                    accept=".zip,.aina.zip,application/zip"
                    className="hidden"
                    aria-label="选择 AINA Project ZIP"
                    onChange={(event) => void importProject(event)}
                  />
                  <button
                    type="button"
                    onClick={() => {
                      setScaffoldOpen((open) => !open);
                      setEditorOpen(false);
                      setNotice(null);
                    }}
                    className="btn-outline"
                    aria-expanded={scaffoldOpen}
                  >
                    <FileArchive className="w-4 h-4" />项目模板
                  </button>
                  <button
                    type="button"
                    disabled={projectAction !== null}
                    onClick={() => projectFileInput.current?.click()}
                    className="btn-outline"
                  >
                    {projectAction === "import" ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
                    {projectAction === "import" ? "正在导入…" : "导入 Project ZIP"}
                  </button>
                </>
              ) : null}
              <button type="button" onClick={() => openEditor()} className="btn-primary">
                <Plus className="w-4 h-4" />注册{tabLabel(tab)}
              </button>
            </div>
          </section>

          {notice ? <Notice {...notice} onClose={() => setNotice(null)} /> : null}

          {editorOpen ? (
            <DefinitionEditor
              tab={tab}
              text={editorText}
              saving={saving}
              onChange={setEditorText}
              onClose={() => setEditorOpen(false)}
              onSave={() => void registerDefinition()}
            />
          ) : null}

          {tab === "aina" && scaffoldOpen ? (
            <ProjectScaffoldForm
              value={scaffold}
              downloading={projectAction === "scaffold"}
              onChange={setScaffold}
              onCancel={() => setScaffoldOpen(false)}
              onSubmit={(event) => void downloadScaffold(event)}
            />
          ) : null}

          {loading ? <LoadingCards /> : null}
          {!loading && tab === "aina" ? (
            <>
              <ProjectSection
                projects={projects}
                busyProjectId={projectAction}
                onDownload={(project) => void downloadProject(project)}
                onDelete={(project) => void deleteProject(project)}
                onDeploy={(project) => void deployProject(project)}
                onUndeploy={(project) => void undeployProject(project)}
              />
              <AinaGrid
                ainas={ainas}
                installedIds={installedIds}
                onInstall={(aina) => void install(aina)}
                onUninstall={(aina) => void uninstall(aina)}
                onOpen={(aina) => void open(aina)}
                onDelete={(id) => void remove("aina", id)}
              />
            </>
          ) : null}
          {!loading && tab === "tools" ? (
            <ToolGrid tools={tools} onDelete={(id) => void remove("tools", id)} />
          ) : null}
          {!loading && tab === "skills" ? (
            <SkillGrid skills={skills} onDelete={(id) => void remove("skills", id)} />
          ) : null}
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
  return (
    <form onSubmit={onSubmit} className="rounded-xl border border-accent-ring bg-white p-4 shadow-soft">
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-soft text-accent">
          <FileArchive className="h-5 w-5" />
        </div>
        <div>
          <h2 className="text-[14px] font-extrabold text-ink">生成 AINA Project 模板</h2>
          <p className="mt-0.5 text-[11.5px] text-ink-muted">下载可编辑的源码 ZIP，开发完成后再导入并校验。</p>
        </div>
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
        <ProjectField label="名称">
          <input
            required
            value={value.name}
            onChange={(event) => onChange({ ...value, name: event.target.value })}
            className="input-soft"
            placeholder="My AINA"
          />
        </ProjectField>
        <ProjectField label="版本">
          <input
            required
            value={value.version ?? ""}
            onChange={(event) => onChange({ ...value, version: event.target.value })}
            className="input-soft"
            placeholder="0.1.0"
          />
        </ProjectField>
        <ProjectField label="运行语言">
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
          <ProjectField label="描述">
            <textarea
              required
              rows={3}
              value={value.description}
              onChange={(event) => onChange({ ...value, description: event.target.value })}
              className="input-soft resize-none"
              placeholder="这个 AINA Project 提供什么能力？"
            />
          </ProjectField>
        </div>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="btn-outline">取消</button>
        <button type="submit" disabled={downloading} className="btn-primary">
          {downloading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          {downloading ? "正在生成…" : "下载 ZIP 模板"}
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

function ProjectSection({
  projects,
  busyProjectId,
  onDownload,
  onDelete,
  onDeploy,
  onUndeploy,
}: {
  projects: AinaProjectRecord[];
  busyProjectId: string | null;
  onDownload: (project: AinaProjectRecord) => void;
  onDelete: (project: AinaProjectRecord) => void;
  onDeploy: (project: AinaProjectRecord) => void;
  onUndeploy: (project: AinaProjectRecord) => void;
}) {
  return (
    <section aria-label="AINA Projects" className="mb-4 rounded-xl border border-line bg-white p-4 shadow-card">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-success-soft text-success-deep">
          <PackageCheck className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="text-[14px] font-extrabold text-ink">AINA Projects</h2>
            <Count value={projects.length} />
          </div>
          <p className="mt-0.5 text-[11.5px] text-ink-muted">源码包经平台校验后独立保存，不会自动安装，也不会进入可调用 AINA 列表。</p>
        </div>
      </div>
      {projects.length ? (
        <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
          {projects.map((project) => {
            const runtime = project.manifest.runtime;
            const managed = runtime.type === "managed";
            const runtimeLabel = managed
              ? `${runtime.language === "python" ? "Python" : "Node.js"} · ${runtime.entrypoint}`
              : runtime.type === "remote"
                ? runtime.endpoint
                : "platform://builtin";
            const busy = busyProjectId === project.id;
            return (
              <article key={project.id} className="rounded-lg border border-line bg-app-soft/50 p-3.5">
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="truncate text-[13.5px] font-extrabold text-ink">{project.manifest.aina.name}</h3>
                      <StatusChip tone={project.status === "deployed" || project.status === "validated" ? "success" : "warning"}>
                        {project.status === "deployed"
                          ? "已部署"
                          : project.status === "validated"
                            ? managed ? "已校验·待部署" : "已校验"
                            : "导入未完成"}
                      </StatusChip>
                    </div>
                    <p className="mt-0.5 truncate font-mono text-[10.5px] text-ink-subtle">
                      {project.manifest.aina.id} · v{project.manifest.aina.version}
                    </p>
                  </div>
                </div>
                <p className="mt-2 line-clamp-2 text-[11.5px] leading-relaxed text-ink-muted">{project.manifest.aina.description}</p>
                <div className="mt-3 space-y-1 rounded-lg bg-white px-2.5 py-2 text-[10.5px] text-ink-muted">
                  <p className="truncate font-mono">{runtimeLabel}</p>
                  <p className="truncate">{project.source_filename} · {project.file_count} 个文件 · {formatBytes(project.size_bytes)}</p>
                  <p className="truncate font-mono" title={project.archive_sha256}>SHA-256 {project.archive_sha256.slice(0, 12)}…</p>
                </div>
                <div className="mt-3 flex items-center gap-2">
                  <span className="text-[10px] text-ink-subtle">更新于 {formatDate(project.updated_at)}</span>
                  <span className="flex-1" />
                  {project.status === "validated" ? (
                    <button
                      type="button"
                      disabled={busyProjectId !== null}
                      onClick={() => onDeploy(project)}
                      className="btn-primary h-8"
                      aria-label={`部署项目 ${project.manifest.aina.name}`}
                    >
                      {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Rocket className="h-3.5 w-3.5" />}
                      {busy ? "部署中…" : "部署"}
                    </button>
                  ) : project.status === "deployed" ? (
                    <button
                      type="button"
                      disabled={busyProjectId !== null}
                      onClick={() => onUndeploy(project)}
                      className="btn-outline h-8"
                      aria-label={`取消部署项目 ${project.manifest.aina.name}`}
                    >
                      {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Unplug className="h-3.5 w-3.5" />}
                      取消部署
                    </button>
                  ) : null}
                  <button
                    type="button"
                    disabled={busyProjectId !== null || project.status === "importing"}
                    onClick={() => onDownload(project)}
                    className="btn-outline h-8"
                    aria-label={`下载项目 ${project.manifest.aina.name}`}
                  >
                    {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                    下载
                  </button>
                  <button
                    type="button"
                    disabled={busyProjectId !== null || project.status === "deployed"}
                    onClick={() => onDelete(project)}
                    className="btn-ghost h-8 text-danger"
                    aria-label={`删除项目 ${project.manifest.aina.name}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="mt-4 rounded-lg border border-dashed border-line-strong bg-app-soft/40 px-4 py-5 text-center">
          <p className="text-[12px] font-bold text-ink">尚未导入 AINA Project</p>
          <p className="mt-1 text-[11px] text-ink-muted">可先下载项目模板，完成开发后导入 ZIP 校验。</p>
        </div>
      )}
    </section>
  );
}

function AinaGrid({
  ainas,
  installedIds,
  onInstall,
  onUninstall,
  onOpen,
  onDelete,
}: {
  ainas: AinaRecord[];
  installedIds: Set<string>;
  onInstall: (aina: AinaRecord) => void;
  onUninstall: (aina: AinaRecord) => void;
  onOpen: (aina: AinaRecord) => void;
  onDelete: (id: string) => void;
}) {
  const [selectedAina, setSelectedAina] = useState<AinaRecord | null>(null);
  if (!ainas.length) return <EmptyState icon={<AppWindow />} title="尚未注册 AINA" detail="请先注册远程运行服务清单。" />;
  return (
    <>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {ainas.map((record) => {
          const manifest = record.manifest;
          const builtin = manifest.runtime.type === "builtin";
          const managed = manifest.runtime.type === "managed";
          const installed = builtin || installedIds.has(manifest.aina.id);
          return (
            <article key={manifest.aina.id} className="rounded-xl border border-line bg-white p-4 shadow-card">
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-xl bg-accent-soft text-accent flex items-center justify-center">
                <Box className="w-5 h-5" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <h2 className="truncate text-[15px] font-extrabold text-ink">{manifest.aina.name}</h2>
                  {builtin ? <StatusChip tone="success">系统内置</StatusChip> : installed ? <StatusChip tone="success">已安装</StatusChip> : <StatusChip>{managed ? "已部署" : "已注册"}</StatusChip>}
                </div>
                <p className="mt-0.5 font-mono text-[10.5px] text-ink-muted">{manifest.aina.id} · v{manifest.aina.version}</p>
              </div>
            </div>
            <p className="mt-3 min-h-[40px] text-[12.5px] leading-relaxed text-ink-muted">{manifest.aina.description}</p>
            <div className="mt-3 rounded-lg bg-app-soft p-2.5 space-y-1.5">
              <div className="flex items-center gap-1.5 text-[11px] text-ink-muted">
                <ExternalLink className="w-3.5 h-3.5" />
                <span className="truncate font-mono">
                  {manifest.runtime.type === "remote"
                    ? manifest.runtime.endpoint
                    : manifest.runtime.type === "managed"
                      ? `本地托管 · ${manifest.runtime.language === "python" ? "Python" : "Node.js"}`
                      : "platform://builtin"}
                </span>
              </div>
              <div className="text-[11px] text-ink-muted">
                {manifest.capabilities.skills.length} 项技能 · {manifest.capabilities.tools.length} 项工具 · {manifest.permissions.length} 项权限
              </div>
            </div>
            {manifest.permissions.length ? (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {manifest.permissions.map((permission) => <span key={permission} className="rounded-md bg-warning-soft px-2 py-1 text-[10px] font-bold text-warning-deep">{permission}</span>)}
              </div>
            ) : null}
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <button type="button" onClick={() => setSelectedAina(record)} className="btn-outline">
                <ListTree className="h-4 w-4" />查看能力
              </button>
              {builtin ? (
                <button type="button" onClick={() => onOpen(record)} className="btn-primary">
                  <ExternalLink className="w-4 h-4" />
                  {manifest.aina.id === "unibot-scheduler" ? "管理任务" : "打开画布"}
                </button>
              ) : installed ? (
                <>
                  {manifest.main_widget ? (
                    <button type="button" onClick={() => onOpen(record)} className="btn-primary">
                      <ExternalLink className="w-4 h-4" />打开画布
                    </button>
                  ) : null}
                  <button type="button" onClick={() => onUninstall(record)} className="btn-outline">
                    <Unplug className="w-4 h-4" />卸载
                  </button>
                </>
              ) : (
                <button type="button" onClick={() => onInstall(record)} className="btn-primary">
                  <Download className="w-4 h-4" />安装并授权
                </button>
              )}
              <span className="flex-1" />
              {!builtin && !managed ? (
                <button type="button" onClick={() => onDelete(manifest.aina.id)} className="btn-ghost text-danger" aria-label={`删除 ${manifest.aina.name}`}>
                  <Trash2 className="w-4 h-4" />
                </button>
              ) : null}
            </div>
            </article>
          );
        })}
      </div>
      {selectedAina ? <AinaCapabilityDialog record={selectedAina} onClose={() => setSelectedAina(null)} /> : null}
    </>
  );
}

function ToolGrid({ tools, onDelete }: { tools: ToolRecord[]; onDelete: (id: string) => void }) {
  if (!tools.length) return <EmptyState icon={<Wrench />} title="尚未注册工具" detail="请注册 OpenAI 函数结构和远程执行地址。" />;
  return (
    <div className="space-y-2.5">
      {tools.map((tool) => (
        <article key={tool.tool_id} className="rounded-xl border border-line bg-white p-4 flex items-center gap-4 shadow-card">
          <div className={classNames("w-10 h-10 rounded-xl flex items-center justify-center", tool.side_effect_level === "high" ? "bg-warning-soft text-warning" : "bg-accent-soft text-accent")}>
            {tool.side_effect_level === "high" ? <ShieldAlert className="w-5 h-5" /> : <Wrench className="w-5 h-5" />}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="text-[14px] font-extrabold text-ink">{tool.name}</h2>
              <StatusChip tone={tool.side_effect_level === "high" ? "warning" : "neutral"}>
                {tool.side_effect_level === "high" ? "需确认" : "无副作用"}
              </StatusChip>
            </div>
            <p className="mt-1 text-[12px] text-ink-muted">{tool.description}</p>
            <p className="mt-1 truncate font-mono text-[10.5px] text-ink-subtle">{tool.tool_id} · {tool.endpoint}</p>
          </div>
          <button type="button" onClick={() => onDelete(tool.tool_id)} className="btn-danger-outline" aria-label={`删除 ${tool.name}`}>
            <Trash2 className="w-4 h-4" />删除
          </button>
        </article>
      ))}
    </div>
  );
}

function SkillGrid({ skills, onDelete }: { skills: SkillRecord[]; onDelete: (id: string) => void }) {
  if (!skills.length) return <EmptyState icon={<Code2 />} title="尚未定义技能" detail="技能为智能体提供可复用的行为指令。" />;
  return (
    <div className="grid grid-cols-2 gap-3">
      {skills.map((skill) => (
        <article key={skill.skill_id} className="rounded-xl border border-line bg-white p-4 shadow-card">
          <div className="flex items-center gap-2">
            <Code2 className="w-5 h-5 text-accent" />
            <h2 className="text-[14px] font-extrabold text-ink">{skill.name}</h2>
            <StatusChip tone={skill.status === "published" ? "success" : "neutral"}>{skill.status}</StatusChip>
          </div>
          <p className="mt-2 text-[12px] leading-relaxed text-ink-muted">{skill.description}</p>
          <div className="mt-3 rounded-lg bg-app-soft p-2.5 text-[11px] leading-relaxed text-ink-muted">{skill.instructions}</div>
          <div className="mt-3 flex items-center gap-2">
            <span className="font-mono text-[10.5px] text-ink-subtle">{skill.skill_id}</span>
            <span className="flex-1" />
            <button type="button" onClick={() => onDelete(skill.skill_id)} className="btn-ghost text-danger" aria-label={`删除 ${skill.name}`}><Trash2 className="w-4 h-4" /></button>
          </div>
        </article>
      ))}
    </div>
  );
}

function DefinitionEditor({ tab, text, saving, onChange, onClose, onSave }: { tab: Tab; text: string; saving: boolean; onChange: (text: string) => void; onClose: () => void; onSave: () => void }) {
  return (
    <section className="rounded-xl border border-accent-ring bg-white shadow-soft overflow-hidden">
      <div className="h-12 border-b border-line bg-accent-soft px-4 flex items-center gap-2">
        <Code2 className="w-4 h-4 text-accent" />
        <h2 className="text-[13px] font-extrabold text-ink">注册{tabLabel(tab)} JSON</h2>
        <span className="flex-1" />
        <button type="button" onClick={onClose} className="btn-ghost h-8" aria-label="关闭注册编辑器"><X className="w-4 h-4" /></button>
      </div>
      <div className="p-4">
        <textarea
          value={text}
          onChange={(event) => onChange(event.target.value)}
          rows={18}
          spellCheck={false}
          aria-label={`${tabLabel(tab)} JSON`}
          className="w-full rounded-lg border border-line-strong bg-slate-950 p-3 font-mono text-[11.5px] leading-relaxed text-slate-100 outline-none focus:border-accent"
        />
        <div className="mt-3 flex items-center gap-2">
          <p className="text-[11px] text-ink-muted">提交前由后端执行数据结构、协议和远程健康检查。</p>
          <span className="flex-1" />
          <button type="button" onClick={onClose} className="btn-outline">取消</button>
          <button type="button" disabled={saving} onClick={onSave} className="btn-primary">{saving ? "正在注册…" : "提交注册"}</button>
        </div>
      </div>
    </section>
  );
}

function TabButton({ active, onClick, icon, children }: { active: boolean; onClick: () => void; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick} className={classNames("h-9 px-3 rounded-lg inline-flex items-center gap-2 text-[12.5px] font-bold border", active ? "bg-accent text-white border-accent" : "bg-app-soft text-ink border-line hover:bg-line/50")}>{icon}{children}</button>
  );
}

function Count({ value }: { value: number }) {
  return <span className="rounded-full bg-black/10 px-1.5 py-0.5 text-[9.5px]">{value}</span>;
}

function StatusChip({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "success" | "warning" }) {
  return <span className={classNames("rounded-md px-1.5 py-0.5 text-[9.5px] font-bold", tone === "success" ? "bg-success-soft text-success-deep" : tone === "warning" ? "bg-warning-soft text-warning-deep" : "bg-app-soft text-ink-muted")}>{children}</span>;
}

function Notice({ tone, text, onClose }: { tone: "success" | "error"; text: string; onClose: () => void }) {
  return (
    <div className={classNames("rounded-lg border p-3 flex items-center gap-2.5", tone === "success" ? "border-success/20 bg-success-soft text-success-deep" : "border-danger-ring bg-danger-soft text-danger-deep")}>
      {tone === "success" ? <CheckCircle2 className="w-4 h-4" /> : <ShieldAlert className="w-4 h-4" />}
      <span className="flex-1 text-[12.5px] font-semibold">{text}</span>
      <button type="button" onClick={onClose} aria-label="关闭提示"><X className="w-4 h-4" /></button>
    </div>
  );
}

function EmptyState({ icon, title, detail }: { icon: React.ReactNode; title: string; detail: string }) {
  return (
    <div className="rounded-xl border border-dashed border-line-strong bg-white py-20 text-center">
      <div className="mx-auto w-10 h-10 text-ink-subtle">{icon}</div>
      <h2 className="mt-3 text-[15px] font-bold text-ink">{title}</h2>
      <p className="mt-1 text-[12px] text-ink-muted">{detail}</p>
    </div>
  );
}

function LoadingCards() {
  return <div className="grid grid-cols-2 gap-3">{[0, 1, 2, 3].map((item) => <div key={item} className="h-48 animate-pulse rounded-xl bg-line/60" />)}</div>;
}

function tabLabel(tab: Tab): string {
  return tab === "aina" ? "AINA" : tab === "tools" ? "工具" : "技能";
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("zh-CN");
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
