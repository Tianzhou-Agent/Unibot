import { useTranslation } from "react-i18next";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  BookOpen,
  Check,
  ExternalLink,
  FileText,
  Info,
  Loader2,
  PanelRight,
  Plug,
  Plus,
  RefreshCw,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { PACKAGE_SANDBOX, URL_SANDBOX } from "@/components/widgets/ainaBridge";
import { api, apiErrorMessage, apiUrl } from "@/lib/api";
import {
  EFFECTS,
  newServer,
  skillFromFile,
  type AuthType,
  type CanvasDraft,
  type CanvasMode,
  type Effect,
  type InspectedTool,
  type ServerDraft,
  type SkillDraft,
} from "@/lib/addApp";
import { classNames } from "@/lib/utils";

export function PartCard({ icon, title, hint, children, invalid }: { icon: ReactNode; title: string; hint?: string; children: ReactNode; invalid?: boolean }) {
  return (
    <section className={classNames("rounded-xl border bg-white p-4", invalid ? "border-danger" : "border-line")} aria-label={title}>
      <div className="flex items-center gap-2.5">
        <span className="text-ink-muted">{icon}</span>
        <h3 className="flex-1 text-[14px] font-semibold text-ink">{title}</h3>
        {hint ? <span className="text-[12px] text-ink-muted">{hint}</span> : null}
      </div>
      <div className="mt-3">{children}</div>
    </section>
  );
}

// --- MCP servers ----------------------------------------------------------------------------------------------

export function McpServersPart({ servers, onChange, invalid }: { servers: ServerDraft[]; onChange: (servers: ServerDraft[]) => void; invalid: boolean }) {
  const { t } = useTranslation("addApp");
  const update = (key: string, change: Partial<ServerDraft>) => onChange(servers.map((server) => (server.key === key ? { ...server, ...change } : server)));
  return (
    <PartCard icon={<Plug className="h-4 w-4" />} title={t("servers.title")} hint={t("servers.hint")} invalid={invalid}>
      <div className="space-y-2">
        {servers.map((server) => (
          <ServerRow
            key={server.key}
            server={server}
            several={servers.length > 1}
            onChange={(change) => update(server.key, change)}
            onRemove={() => onChange(servers.filter((item) => item.key !== server.key))}
          />
        ))}
        <button type="button" onClick={() => onChange([...servers, newServer(servers.length)])} className="btn-outline w-full justify-center rounded-lg">
          <Plus className="h-4 w-4" />{t("servers.add")}
        </button>
      </div>
    </PartCard>
  );
}

function ServerRow({ server, several, onChange, onRemove }: { server: ServerDraft; several: boolean; onChange: (change: Partial<ServerDraft>) => void; onRemove: () => void }) {
  const { t } = useTranslation("addApp");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ tools: InspectedTool[] }>("/ainas/mcp/inspect", {
        url: server.url.trim(),
        auth: { type: server.auth },
        credential: server.credential.trim() || undefined,
      });
      const kept = Object.fromEntries(Object.entries(server.selected).filter(([name]) => result.tools.some((tool) => tool.name === name)));
      const selected = Object.keys(kept).length ? kept : Object.fromEntries(result.tools.map((tool) => [tool.name, tool.effect]));
      onChange({ tools: result.tools, selected });
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setBusy(false);
    }
  }

  const listed = server.tools ?? Object.keys(server.selected).map((name) => ({ name, description: "", effect: server.selected[name] }));
  return (
    <div className="rounded-lg border border-line p-3">
      <div className="flex flex-wrap items-center gap-2">
        {several ? (
          <input
            value={server.id}
            onChange={(event) => onChange({ id: event.target.value.toLowerCase() })}
            className="input-soft w-28"
            aria-label={t("servers.id")}
            title={t("servers.id")}
          />
        ) : null}
        <input
          value={server.url}
          onChange={(event) => onChange({ url: event.target.value, tools: null })}
          placeholder="https://tools.example.com/mcp"
          aria-label={t("servers.url")}
          className="input-soft min-w-0 flex-1"
        />
        <select value={server.auth} onChange={(event) => onChange({ auth: event.target.value as AuthType })} className="input-soft w-auto" aria-label={t("servers.auth")}>
          {(["none", "bearer", "api_key", "oauth2"] as const).map((type) => <option key={type} value={type}>{t(`servers.authType.${type}`)}</option>)}
        </select>
        <button type="button" onClick={() => void load()} disabled={busy || !server.url.trim()} className="btn-outline rounded-lg">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}{t("servers.load")}
        </button>
        <button type="button" onClick={onRemove} className="flex h-8 w-8 items-center justify-center rounded-lg text-ink-muted hover:bg-app-soft hover:text-ink" aria-label={t("servers.remove")}>
          <Trash2 className="h-4 w-4" />
        </button>
      </div>
      {server.auth !== "none" ? (
        <label className="mt-2 block">
          <input
            type="password"
            autoComplete="off"
            value={server.credential}
            onChange={(event) => onChange({ credential: event.target.value })}
            placeholder={t(server.auth === "oauth2" ? "servers.credentialOauth" : "servers.credential")}
            aria-label={t("servers.credentialLabel")}
            className="input-soft"
          />
          <span className="mt-1 block text-[11px] text-ink-muted">{t(server.auth === "oauth2" ? "servers.oauthHint" : "servers.secretHint")}</span>
        </label>
      ) : null}
      {error ? <p role="alert" className="mt-2 text-[12px] text-danger">{error}</p> : null}
      {listed.length ? (
        <div className="mt-3 border-t border-line">
          <div className="flex items-center justify-between py-2 text-[11.5px] text-ink-muted">
            <span>{t("servers.included", { count: Object.keys(server.selected).length, total: listed.length })}</span>
            {server.tools ? (
              <span className="flex gap-3">
                <button type="button" className="hover:text-ink" onClick={() => onChange({ selected: Object.fromEntries(listed.map((tool) => [tool.name, tool.effect])) })}>{t("servers.all")}</button>
                <button type="button" className="hover:text-ink" onClick={() => onChange({ selected: {} })}>{t("servers.none")}</button>
              </span>
            ) : null}
          </div>
          <ul className="max-h-72 space-y-1 overflow-y-auto">
            {listed.map((tool) => (
              <ToolRow
                key={tool.name}
                tool={tool}
                effect={server.selected[tool.name]}
                onChange={(effect) => {
                  const selected = { ...server.selected };
                  if (effect) selected[tool.name] = effect;
                  else delete selected[tool.name];
                  onChange({ selected });
                }}
              />
            ))}
          </ul>
        </div>
      ) : (
        <p className="mt-2 text-[11.5px] text-ink-muted">{t("servers.loadHint")}</p>
      )}
    </div>
  );
}

function ToolRow({ tool, effect, onChange }: { tool: InspectedTool; effect: Effect | undefined; onChange: (effect: Effect | null) => void }) {
  const { t } = useTranslation("addApp");
  // A tool may only be made stricter than its own annotations say (the host applies the stricter one anyway).
  const options = EFFECTS.slice(EFFECTS.indexOf(tool.min_effect ?? "read"));
  return (
    <li className="flex items-center gap-2 text-[12.5px]">
      <input type="checkbox" checked={Boolean(effect)} onChange={(event) => onChange(event.target.checked ? tool.effect : null)} aria-label={t("servers.include", { name: tool.name })} />
      <span className="min-w-0 flex-1">
        <code className="font-mono text-ink">{tool.name}</code>
        {tool.description ? <span className="ml-2 truncate text-ink-muted">{tool.description.slice(0, 90)}</span> : null}
      </span>
      {effect ? (
        <select value={effect} onChange={(event) => onChange(event.target.value as Effect)} className="input-soft h-7 w-auto py-0 text-[12px]" aria-label={t("servers.effect", { name: tool.name })}>
          {options.map((option) => <option key={option} value={option}>{t(`effect.${option}`)}</option>)}
        </select>
      ) : <span className="text-[11.5px] text-ink-muted">{t("servers.excluded")}</span>}
    </li>
  );
}

// --- Skills ---------------------------------------------------------------------------------------------------

export function SkillsPart({ skills, onChange }: { skills: SkillDraft[]; onChange: (skills: SkillDraft[]) => void }) {
  const { t } = useTranslation("addApp");
  const input = useRef<HTMLInputElement>(null);
  async function add(files: FileList | null) {
    const added = await Promise.all(Array.from(files ?? []).map(skillFromFile));
    const taken = new Set(skills.map((skill) => skill.id));
    onChange([...skills, ...added.map((skill) => (taken.has(skill.id) ? { ...skill, id: `${skill.id}-${skill.key.split("-").pop()}` } : skill))]);
  }
  return (
    <PartCard icon={<BookOpen className="h-4 w-4" />} title={t("skills.title")} hint={t("skills.hint")}>
      {skills.length ? (
        <ul className="mb-3 space-y-1.5">
          {skills.map((skill) => (
            <li key={skill.key} className="flex items-center gap-2 text-[12.5px]">
              <FileText className="h-4 w-4 shrink-0 text-ink-muted" />
              <span className="min-w-0 flex-1 truncate">
                <span className="font-semibold text-ink">{skill.name || skill.file?.name || skill.id}</span>
                <span className="ml-2 text-ink-muted">{skill.description || (skill.file ? t("skills.readOnCheck") : "")}</span>
              </span>
              <button type="button" onClick={() => onChange(skills.filter((item) => item.key !== skill.key))} className="text-ink-muted hover:text-ink" aria-label={t("skills.remove", { name: skill.name || skill.id })}>
                <X className="h-4 w-4" />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <button type="button" onClick={() => input.current?.click()} className="btn-outline rounded-lg">
        <Upload className="h-4 w-4" />{t("skills.add")}
      </button>
      <input ref={input} type="file" accept=".md,.zip" multiple hidden data-testid="skill-files" onChange={(event) => { void add(event.target.files); event.target.value = ""; }} />
    </PartCard>
  );
}

// --- Canvas ---------------------------------------------------------------------------------------------------

const WIDTHS = { pane: 640, expanded: 0, phone: 375 } as const;

export function CanvasPart({
  canvas,
  fromPackage,
  preview,
  onChange,
}: {
  canvas: CanvasDraft;
  fromPackage: boolean;
  /** Where a checked bundle (or the uploaded package's canvas) is served. */
  preview: string | null;
  onChange: (canvas: CanvasDraft) => void;
}) {
  const { t } = useTranslation("addApp");
  const bundleInput = useRef<HTMLInputElement>(null);
  // The preview follows the address once typing stops, not on every keystroke.
  const [previewUrl, setPreviewUrl] = useState(canvas.url.trim());
  useEffect(() => {
    const timer = window.setTimeout(() => setPreviewUrl(canvas.url.trim()), 700);
    return () => window.clearTimeout(timer);
  }, [canvas.url]);
  const modes: CanvasMode[] = ["none", "url", "bundle", ...(fromPackage ? (["package"] as const) : [])];
  return (
    <PartCard icon={<PanelRight className="h-4 w-4" />} title={t("canvas.title")} hint={t("canvas.hint")}>
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label={t("canvas.source")}>
        {modes.map((mode) => (
          <button
            key={mode}
            type="button"
            role="radio"
            aria-checked={canvas.mode === mode}
            onClick={() => onChange({ ...canvas, mode })}
            className={classNames("rounded-lg border px-3 py-1.5 text-[12.5px]", canvas.mode === mode ? "border-accent bg-accent-soft text-accent" : "border-line text-ink-muted hover:text-ink")}
          >
            {t(`canvas.mode.${mode}`)}
          </button>
        ))}
      </div>
      {canvas.mode !== "none" ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {canvas.mode === "url" ? (
            <input value={canvas.url} onChange={(event) => onChange({ ...canvas, url: event.target.value })} placeholder="https://app.example.com/" aria-label={t("canvas.url")} className="input-soft min-w-0 flex-1" />
          ) : null}
          {canvas.mode === "bundle" ? (
            <>
              <button type="button" onClick={() => bundleInput.current?.click()} className="btn-outline rounded-lg">
                <Upload className="h-4 w-4" />{canvas.bundle ? canvas.bundle.name : t("canvas.chooseBundle")}
              </button>
              <input ref={bundleInput} type="file" accept=".zip" hidden data-testid="canvas-bundle" onChange={(event) => { onChange({ ...canvas, bundle: event.target.files?.[0] ?? null }); event.target.value = ""; }} />
            </>
          ) : null}
          <input value={canvas.title} onChange={(event) => onChange({ ...canvas, title: event.target.value })} placeholder={t("canvas.titlePlaceholder")} aria-label={t("canvas.titleLabel")} className="input-soft w-44" />
        </div>
      ) : null}
      {canvas.mode === "url" && previewUrl ? <CanvasPreview src={previewUrl} kind="url" title={canvas.title} /> : null}
      {canvas.mode === "bundle" || canvas.mode === "package" ? (
        preview ? <CanvasPreview src={apiUrl(preview)} kind="package" title={canvas.title} /> : <p className="mt-3 text-[11.5px] text-ink-muted">{t("canvas.checkToPreview")}</p>
      ) : null}
    </PartCard>
  );
}

function CanvasPreview({ src, kind, title }: { src: string; kind: "url" | "package"; title: string }) {
  const { t } = useTranslation("addApp");
  const frame = useRef<HTMLIFrameElement>(null);
  const [width, setWidth] = useState<keyof typeof WIDTHS>("pane");
  const [reload, setReload] = useState(0);
  const [bridge, setBridge] = useState<"waiting" | "connected" | "none">("waiting");
  const [inspection, setInspection] = useState<{ allowed: boolean; embeddable?: boolean | null; detail?: string | null } | null>(null);

  // The page talks to Superbot through @unibot/aina-ui; any message from the frame shows the bridge is there.
  useEffect(() => {
    setBridge("waiting");
    const listen = (event: MessageEvent) => { if (event.source === frame.current?.contentWindow) setBridge("connected"); };
    window.addEventListener("message", listen);
    const timer = window.setTimeout(() => setBridge((current) => (current === "waiting" ? "none" : current)), 4000);
    return () => { window.removeEventListener("message", listen); window.clearTimeout(timer); };
  }, [src, reload]);

  useEffect(() => {
    if (kind !== "url") return;
    let active = true;
    setInspection(null);
    api.post<{ allowed: boolean; embeddable?: boolean | null; detail?: string | null }>("/ainas/canvas/inspect", { url: src })
      .then((result) => { if (active) setInspection(result); })
      .catch((error) => { if (active) setInspection({ allowed: false, detail: apiErrorMessage(error) }); });
    return () => { active = false; };
  }, [kind, src]);

  const host = (() => { try { return new URL(src, window.location.href).host; } catch { return src; } })();
  return (
    <div className="mt-3">
      <div className="mb-1.5 flex flex-wrap items-center gap-2 text-[11.5px] text-ink-muted">
        <span className="flex-1">{t("canvas.previewTitle")}</span>
        <label className="flex items-center gap-1">
          {t("canvas.width")}
          <select value={width} onChange={(event) => setWidth(event.target.value as keyof typeof WIDTHS)} className="input-soft h-7 w-auto py-0 text-[11.5px]">
            {(Object.keys(WIDTHS) as (keyof typeof WIDTHS)[]).map((key) => <option key={key} value={key}>{t(`canvas.widths.${key}`)}</option>)}
          </select>
        </label>
        <button type="button" onClick={() => setReload((value) => value + 1)} className="hover:text-ink" aria-label={t("canvas.reload")}><RefreshCw className="h-3.5 w-3.5" /></button>
        <a href={src} target="_blank" rel="noreferrer noopener" className="hover:text-ink" aria-label={t("canvas.openTab")}><ExternalLink className="h-3.5 w-3.5" /></a>
      </div>
      <div className="mx-auto overflow-hidden rounded-lg border border-line" style={{ maxWidth: WIDTHS[width] || undefined }}>
        <div className="flex items-center gap-2 border-b border-line bg-app-soft px-3 py-1.5 text-[11.5px]">
          <PanelRight className="h-3.5 w-3.5 text-ink-muted" />
          <span className="font-semibold text-ink">{title || t("canvas.untitled")}</span>
          <span className="truncate text-ink-muted">{host}</span>
        </div>
        <iframe
          key={`${src}:${reload}`}
          ref={frame}
          src={src}
          title={t("canvas.previewTitle")}
          sandbox={kind === "url" ? URL_SANDBOX : PACKAGE_SANDBOX}
          referrerPolicy="no-referrer"
          className="h-[420px] w-full border-0 bg-white"
        />
      </div>
      <ul className="mt-2 space-y-1 text-[11.5px]">
        {kind === "url" ? (
          inspection === null ? <Status tone="muted" text={t("canvas.checking")} /> : (
            <>
              <Status tone={inspection.allowed ? "ok" : "warn"} text={inspection.allowed ? t("canvas.allowed") : inspection.detail || t("canvas.notAllowed")} />
              {inspection.allowed ? (
                <Status
                  tone={inspection.embeddable === false ? "warn" : inspection.embeddable ? "ok" : "muted"}
                  text={inspection.embeddable ? t("canvas.embeddable") : inspection.detail || t("canvas.embeddableUnknown")}
                />
              ) : null}
            </>
          )
        ) : null}
        <Status
          tone={bridge === "connected" ? "ok" : bridge === "none" ? "info" : "muted"}
          text={t(`canvas.bridge.${bridge}`)}
          link={bridge === "none" ? { to: "/plugin/help/canvas-bridge", label: t("canvas.bridge.learn") } : undefined}
        />
      </ul>
    </div>
  );
}

function Status({ tone, text, link }: { tone: "ok" | "warn" | "info" | "muted"; text: string; link?: { to: string; label: string } }) {
  const Icon = tone === "ok" ? Check : tone === "warn" ? AlertTriangle : tone === "info" ? Info : Loader2;
  return (
    <li className={classNames("flex items-start gap-1.5", tone === "ok" ? "text-success" : tone === "warn" ? "text-warning-deep" : "text-ink-muted")}>
      <Icon className={classNames("mt-0.5 h-3.5 w-3.5 shrink-0", tone === "muted" && "animate-spin")} />
      <span>
        {text}
        {/* A new tab, so the form being filled in is kept. */}
        {link ? <> <Link to={link.to} target="_blank" className="text-accent hover:underline">{link.label}</Link></> : null}
      </span>
    </li>
  );
}
