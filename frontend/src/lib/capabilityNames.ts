import { useSyncExternalStore } from "react";
import { api } from "@/lib/api";
import type { AinaRecord } from "@/types";

// A capability's function name is `<kind>_<capability id>_<digest>` (backend capabilities.function_name), and an
// app's capability ids start with its AINA id. The registered apps, fetched once, tell where the app id ends.
const FUNCTION_NAME = /^(aina|mcp|canvas|load_skill|tool|builtin)_(.+)_[0-9a-f]{8}$/;

interface AppNames {
  /** The AINA id as function names spell it. */
  key: string;
  name: string;
  /** The publisher's titles of the app's tools, by tool name as function names spell it. */
  titles: Map<string, string>;
}

let apps: AppNames[] = [];
let request: Promise<void> | null = null;
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  request ??= api.get<AinaRecord[]>("/ainas").then((records) => {
    // Longest first, so an app whose id extends another's is matched before it.
    apps = records.map(appNames).sort((a, b) => b.key.length - a.key.length);
    listeners.forEach((notify) => notify());
  }, () => {
    request = null;
  });
  return () => {
    listeners.delete(listener);
  };
}

function appNames({ manifest }: AinaRecord): AppNames {
  const tools = manifest.runtime.type === "package"
    ? [...manifest.runtime.tools, ...(manifest.runtime.package.canvas?.tools ?? [])]
    : [];
  return {
    key: spelled(manifest.aina.id),
    name: manifest.aina.name,
    titles: new Map(tools.flatMap((tool): [string, string][] => tool.title ? [[spelled(tool.name), tool.title]] : [])),
  };
}

function spelled(id: string): string {
  return id.replace(/[^A-Za-z0-9_-]/g, "_");
}

/** Re-renders the caller once the app names have loaded, so `describeCapability` can name apps. */
export function useCapabilityNames() {
  useSyncExternalStore(subscribe, () => apps);
}

/** The app and readable action behind a capability function name, or null for a name of another form. */
export function describeCapability(functionName: string): { kind: string; app: string | null; action: string } | null {
  const match = FUNCTION_NAME.exec(functionName);
  if (!match) return null;
  const [, kind, id] = match;
  const app = apps.find((entry) => id === entry.key || id.startsWith(`${entry.key}_`));
  // An app that is no longer registered: its id is a reverse domain name of at least two parts, like local.pptist.
  const parts = id.split("_");
  const tool = app ? id.slice(app.key.length + 1)
    : (kind === "mcp" || kind === "canvas") && parts.length > 2 ? parts.slice(2).join("_") : id;
  return { kind, app: app?.name ?? null, action: app?.titles.get(tool) ?? humanize(tool) };
}

function humanize(name: string): string {
  const words = name.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/[_.-]+/g, " ").trim().toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
