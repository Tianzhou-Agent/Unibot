// The Add app page's model: what the developer filled in, and the aina.json it becomes (backend: aina_add_app.py).
export type Effect = "read" | "write" | "destructive";
export type AuthType = "none" | "bearer" | "api_key" | "oauth2";

export const EFFECTS: Effect[] = ["read", "write", "destructive"];

export interface InspectedTool {
  name: string;
  title?: string | null;
  description: string;
  effect: Effect;
  /** The loosest effect the server's annotations allow. */
  min_effect?: Effect;
}

export interface ServerDraft {
  key: string;
  id: string;
  url: string;
  auth: AuthType;
  /** Used once to list the tools of a server that needs it; never stored in the package. */
  credential: string;
  tools: InspectedTool[] | null;
  /** Chosen tools and their effect. */
  selected: Record<string, Effect>;
}

export interface SkillDraft {
  key: string;
  id: string;
  name: string;
  description: string;
  /** A new SKILL.md or folder .zip; absent for a skill kept from the uploaded package. */
  file: File | null;
}

export type CanvasMode = "none" | "url" | "bundle" | "package";

export interface CanvasDraft {
  mode: CanvasMode;
  title: string;
  url: string;
  bundle: File | null;
}

export interface AppDraft {
  id: string;
  idEdited: boolean;
  version: string;
  name: string;
  description: string;
  servers: ServerDraft[];
  skills: SkillDraft[];
  canvas: CanvasDraft;
}

type Json = Record<string, unknown>;

export const MAX_DESCRIPTION = 1000;
export const ID_PATTERN = /^[a-z0-9]+(\.[a-z0-9-]+)+$/;
const LOCAL_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;

export function emptyDraft(): AppDraft {
  return {
    id: "",
    idEdited: false,
    version: "0.1.0",
    name: "",
    description: "",
    servers: [],
    skills: [],
    canvas: { mode: "none", title: "", url: "", bundle: null },
  };
}

let counter = 0;
export function draftKey(): string {
  counter += 1;
  return `part-${counter}`;
}

export function slug(value: string): string {
  return value.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 63);
}

/** The app ID derived from its name until the developer edits it. */
export function suggestedId(name: string): string {
  const part = slug(name);
  return part ? `local.${part}` : "";
}

export function newServer(index: number): ServerDraft {
  return { key: draftKey(), id: index === 0 ? "main" : `server-${index + 1}`, url: "", auth: "none", credential: "", tools: null, selected: {} };
}

/** A skill's id, name and description from its SKILL.md frontmatter (a .zip is read by the server). */
export async function skillFromFile(file: File): Promise<SkillDraft> {
  const base = slug(file.name.replace(/\.(zip|md)$/i, "")) || "skill";
  const draft: SkillDraft = { key: draftKey(), id: base === "skill" ? `skill-${counter}` : base, name: "", description: "", file };
  if (/\.zip$/i.test(file.name)) return draft;
  const match = /^---\r?\n([\s\S]*?)\r?\n---/.exec(await file.text());
  for (const line of match?.[1].split(/\r?\n/) ?? []) {
    const [, key, value] = /^(\w+):\s*(.*)$/.exec(line) ?? [];
    const text = value?.replace(/^["']|["']$/g, "").trim();
    if (key === "name" && text) {
      draft.name = text;
      if (LOCAL_ID_PATTERN.test(slug(text))) draft.id = slug(text);
    }
    if (key === "description" && text) draft.description = text;
  }
  return draft;
}

/** The next patch version (``1.2.3`` → ``1.2.4``); a version that is not ``x.y.z`` is left for the developer. */
export function nextVersion(version: string): string {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(version);
  return match ? `${match[1]}.${match[2]}.${Number(match[3]) + 1}` : version;
}

/** The version to save a package as when ``registered`` is taken: its own if newer, else the next patch. */
export function releaseVersion(version: string, registered: string): string {
  const parts = (value: string) => /^(\d+)\.(\d+)\.(\d+)$/.exec(value)?.slice(1).map(Number);
  const own = parts(version);
  const taken = parts(registered);
  if (!own || !taken) return version === registered ? nextVersion(registered) : version;
  const newer = own[0] - taken[0] || own[1] - taken[1] || own[2] - taken[2];
  return newer > 0 ? version : nextVersion(registered);
}

/** Fill the form from a package's manifest as the validator returned it. */
export function draftFromManifest(manifest: Json, skills: { id: string; name: string; description: string }[]): AppDraft {
  const aina = (manifest.aina ?? {}) as Json;
  const tools = manifest.tools as Json | null | undefined;
  const servers = tools ? ([] as Json[]).concat(tools.mcp as Json | Json[]) : [];
  const expose = (tools?.expose ?? {}) as Record<string, Json>;
  const canvas = manifest.canvas as Json | null | undefined;
  return {
    id: String(aina.id ?? ""),
    idEdited: true,
    version: String(aina.version ?? "0.1.0"),
    name: String(aina.name ?? ""),
    description: String(aina.description ?? ""),
    servers: servers.map((server, index) => {
      const id = String(server.id ?? (index === 0 ? "main" : `server-${index + 1}`));
      const selected: Record<string, Effect> = {};
      for (const [name, tool] of Object.entries(expose)) {
        if (servers.length === 1 || tool.server === server.id) selected[name] = (tool.effect as Effect | null) ?? "write";
      }
      return { key: draftKey(), id, url: String(server.url ?? ""), auth: ((server.auth as Json)?.type as AuthType) ?? "none", credential: "", tools: null, selected };
    }),
    skills: skills.map((skill) => ({ key: draftKey(), id: skill.id, name: skill.name, description: skill.description, file: null })),
    canvas: canvas
      ? { mode: canvas.url ? "url" : "package", title: String(canvas.title ?? ""), url: String(canvas.url ?? ""), bundle: null }
      : { mode: "none", title: "", url: "", bundle: null },
  };
}

/** aina.json for the draft, keeping what the form does not edit (widgets, permissions, locales…) from ``base``. */
export function buildManifest(draft: AppDraft, base: Json | null): Json {
  const manifest = withoutNulls(base ?? {}) as Json;
  const baseAina = (manifest.aina ?? {}) as Json;
  const prefix = draft.id.split(".")[0] || "local";
  manifest.protocol_version = "2.0";
  delete manifest.$schema;
  manifest.aina = {
    ...baseAina,
    id: draft.id,
    version: draft.version,
    name: draft.name.trim(),
    description: draft.description.trim(),
    publisher: (baseAina.publisher as Json | undefined) ?? { id: prefix, name: draft.name.trim() || prefix },
  };

  const baseExpose = ((manifest.tools as Json | undefined)?.expose ?? {}) as Record<string, Json>;
  const servers = draft.servers.filter((server) => server.url.trim());
  const config = ((manifest.config as Json | undefined)?.schema as Json | undefined) ?? { type: "object", properties: {} };
  const properties = { ...((config.properties as Json | undefined) ?? {}) };
  if (servers.length) {
    const several = servers.length > 1;
    const expose: Record<string, Json> = {};
    const mcp = servers.map((server) => {
      const auth: Json = { type: server.auth };
      if (server.auth === "bearer" || server.auth === "api_key") {
        // The user enters the server's token when installing the app.
        const field = `${server.id.replace(/-/g, "_")}_token`;
        properties[field] = { type: "string", title: `${server.id} token`, "x-secret": true };
        auth.secret = field;
      }
      for (const [name, effect] of Object.entries(server.selected)) {
        const { server: _ignored, ...kept } = baseExpose[name] ?? {};
        expose[name] = { ...kept, effect, ...(several ? { server: server.id } : {}) };
      }
      return several ? { id: server.id, url: server.url.trim(), auth } : { url: server.url.trim(), auth };
    });
    manifest.tools = { mcp: several ? mcp : mcp[0], expose };
  } else {
    delete manifest.tools;
  }
  if (Object.keys(properties).length) manifest.config = { schema: { ...config, properties } };

  if (draft.skills.length) {
    const baseSkills = new Map(((manifest.skills as Json[] | undefined) ?? []).map((skill) => [skill.id, skill]));
    manifest.skills = draft.skills.map((skill) => (skill.file ? { id: skill.id } : baseSkills.get(skill.id) ?? { id: skill.id }));
  } else {
    delete manifest.skills;
  }

  const baseCanvas = (manifest.canvas ?? {}) as Json;
  const title = draft.canvas.title.trim() || draft.name.trim();
  if (draft.canvas.mode === "url") {
    const { entry: _entry, ...kept } = baseCanvas;
    manifest.canvas = { ...kept, title, url: draft.canvas.url.trim() };
  } else if (draft.canvas.mode === "bundle" || draft.canvas.mode === "package") {
    const { url: _url, ...kept } = baseCanvas;
    manifest.canvas = { ...kept, title };
  } else {
    delete manifest.canvas;
  }
  return manifest;
}

/** Problems the page can name before asking the server. */
export function draftProblems(draft: AppDraft): string[] {
  const problems: string[] = [];
  if (!draft.name.trim()) problems.push("name");
  if (!draft.description.trim()) problems.push("description");
  if (!ID_PATTERN.test(draft.id)) problems.push("id");
  const servers = draft.servers.filter((server) => server.url.trim());
  const hasCanvas = draft.canvas.mode !== "none" && (draft.canvas.mode !== "url" || draft.canvas.url.trim()) && (draft.canvas.mode !== "bundle" || draft.canvas.bundle);
  if (!servers.length && !draft.skills.length && !hasCanvas) problems.push("parts");
  if (servers.some((server) => !Object.keys(server.selected).length)) problems.push("tools");
  if (duplicateTools(draft).length) problems.push("duplicate");
  return problems;
}

/** Tool names chosen on more than one server: an app's tool names are unique. */
export function duplicateTools(draft: AppDraft): string[] {
  const names = draft.servers.flatMap((server) => (server.url.trim() ? Object.keys(server.selected) : []));
  return [...new Set(names.filter((name, index) => names.indexOf(name) !== index))];
}

/** The multipart body of POST /ainas/packages/compose. */
export function composeForm(draft: AppDraft, base: { manifest: Json; file: File } | null): FormData {
  const form = new FormData();
  form.append("manifest", JSON.stringify(buildManifest(draft, base?.manifest ?? null)));
  if (base) form.append("base", base.file);
  for (const skill of draft.skills) if (skill.file) form.append(`skill:${skill.id}`, skill.file);
  if (draft.canvas.mode === "bundle" && draft.canvas.bundle) form.append("canvas_bundle", draft.canvas.bundle);
  const credential = draft.servers.find((server) => server.credential.trim())?.credential.trim();
  if (credential) form.append("credential", credential);
  return form;
}

function withoutNulls(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(withoutNulls);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Json).filter(([, item]) => item !== null && item !== undefined).map(([key, item]) => [key, withoutNulls(item)]),
    );
  }
  return value;
}
