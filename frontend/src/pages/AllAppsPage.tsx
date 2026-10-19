import { useTranslation } from "react-i18next";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AppWindow,
  CheckCircle2,
  Code2,
  Download,
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
  Wrench,
  X,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { InstallConsentDialog } from "@/components/apps/InstallConsentDialog";
import { ActionMenu, AppIcon, PluginChip, PluginRow, PluginSection, type MenuItem } from "@/components/apps/PluginUi";
import { Topbar } from "@/components/layout/Topbar";
import { api, apiErrorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ACTOR_QUERY, deleteAina, installAina, isSystemAina, openAina, uninstallAina } from "@/lib/plugins";
import { classNames, downloadBlob } from "@/lib/utils";
import type {
  AinaInstallation,
  AinaProjectRecord,
  AinaRecord,
  SkillRecord,
  ToolRecord,
} from "@/types";

type View = "apps" | "developer";
type Kind = "aina" | "tools" | "skills";

export default function AllAppsPage() {
  const { t } = useTranslation("apps");
  const navigate = useNavigate();
  const { user, config } = useAuth();
  const canManageRegistry = !config.auth_required || Boolean(user?.is_admin);
  const [view, setView] = useState<View>("apps");
  const [query, setQuery] = useState("");
  const [ainas, setAinas] = useState<AinaRecord[]>([]);
  const [consentFor, setConsentFor] = useState<AinaRecord | null>(null);
  const [projects, setProjects] = useState<AinaProjectRecord[]>([]);
  const [installations, setInstallations] = useState<AinaInstallation[]>([]);
  const [tools, setTools] = useState<ToolRecord[]>([]);
  const [skills, setSkills] = useState<SkillRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [projectAction, setProjectAction] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);

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

  async function install(aina: AinaRecord) {
    if (aina.manifest.runtime.type === "package") {
      setConsentFor(aina); // a 2.0 package installs only after the user reviews its access and settings
      return;
    }
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
  const usable = (record: AinaRecord) => isSystemAina(record.manifest.runtime) || installedIds.has(record.manifest.aina.id);
  const visibleProjects = projects.filter(({ manifest }) => matches(manifest.aina.name, manifest.aina.description, manifest.aina.id));
  const visibleTools = tools.filter((tool) => matches(tool.name, tool.description, tool.tool_id));
  const visibleSkills = skills.filter((skill) => matches(skill.name, skill.description, skill.skill_id));

  return (
    <div className="flex h-full flex-col bg-app-bg">
      {consentFor ? (
        <InstallConsentDialog
          record={consentFor}
          onClose={() => setConsentFor(null)}
          onInstalled={() => {
            setNotice({ tone: "success", text: t("notice.installed", { name: consentFor.manifest.aina.name }) });
            setConsentFor(null);
            void load();
          }}
        />
      ) : null}
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
              <button
                type="button"
                onClick={() => navigate("/plugin/new")}
                className="btn h-10 shrink-0 gap-1.5 rounded-full bg-ink px-4 text-white hover:bg-black"
              >
                <Plus className="h-4 w-4" />{t("add")}
              </button>
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
  const builtin = isSystemAina(runtime);
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

// Explanatory copy sits behind an icon instead of a paragraph: hover shows it, screen readers read it.
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

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
