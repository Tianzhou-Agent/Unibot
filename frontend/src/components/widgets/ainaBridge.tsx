import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { ShieldAlert } from "lucide-react";
import i18n from "@/i18n";
import { api, apiErrorMessage, apiUrl } from "@/lib/api";
import { classNames } from "@/lib/utils";
import type { AinaInstallation, AinaRecord } from "@/types";

// The UI bridge of AINA Protocol 2.0 (design §9.4), host side, shared by canvases and widgets. An AINA's UI runs in
// a sandboxed iframe and can only ask: the host answers messages from that iframe's own window alone, relays
// app-visible tool calls only, and asks the user itself before any call that changes data.
export const PROTOCOL = "aina-ui/2.0";
export const PACKAGE_SANDBOX = "allow-scripts allow-forms allow-downloads";
export const URL_SANDBOX = "allow-scripts allow-same-origin allow-forms allow-popups allow-downloads";
const THEME: Record<string, string> = {
  "--aina-bg": "#FFFFFF",
  "--aina-surface": "#FAFAFA",
  "--aina-fg": "#0F1115",
  "--aina-muted": "#61666B",
  "--aina-border": "rgba(0, 0, 0, 0.10)",
  "--aina-accent": "#4176E6",
  "--aina-danger": "#DC2626",
  "--aina-radius": "12px",
  "--aina-font": '"Noto Sans SC", "Inter", system-ui, -apple-system, "Segoe UI", sans-serif',
};

export type PackageTool = Extract<AinaRecord["manifest"]["runtime"], { type: "package" }>["tools"][number];

type BridgeRequest = { unibot: string; id: number | string; method?: string; params?: Record<string, unknown> };
type BridgeReply = { unibot: string; id: number | string; result?: unknown; error?: { code?: string; message?: string } };

export class BridgeError extends Error {
  constructor(readonly code: string, message: string) {
    super(message);
  }
}

type Confirmation = { tool: PackageTool; args: Record<string, unknown>; resolve: (allowed: boolean) => void };

/** URL of a packaged UI file (served sandboxed by the backend). */
export function packageUiUrl(ainaId: string, version: string, entry: string): string {
  const path = entry.split("/").map(encodeURIComponent).join("/");
  return apiUrl(`/aina-ui/${encodeURIComponent(ainaId)}/${encodeURIComponent(version)}/${path}`);
}

/**
 * Runs the bridge for one frame. ``extra`` answers the methods specific to a canvas or widget; ``initialize``,
 * ``callTool`` and ``sendPrompt`` are common.
 */
export function useAinaBridge({
  ainaId,
  frame,
  origin,
  source,
  context,
  onPrompt,
  extra,
}: {
  ainaId: string;
  frame: RefObject<HTMLIFrameElement | null>;
  /** The URL canvas's origin; null for packaged UI, whose origin is opaque. */
  origin: string | null;
  source: "canvas" | "widget";
  context: Record<string, unknown>;
  onPrompt?: (prompt: string) => void;
  extra?: (method: string, params: Record<string, unknown>) => Promise<unknown> | undefined;
}) {
  const { t } = useTranslation("canvas");
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const pending = useRef<Confirmation | null>(null);
  // Requests the host sends to the frame (canvas tool calls), by id.
  const hostRequests = useRef(new Map<string, { resolve: (value: unknown) => void; reject: (error: Error) => void }>());
  const nextHostRequest = useRef(1);
  const approvedWrites = useRef(new Set<string>());
  const latest = useRef({ context, onPrompt, extra });
  latest.current = { context, onPrompt, extra };

  // What the bridge needs before it answers: the reviewed tools and this user's (non-secret) configuration.
  const loaded = useMemo(
    () =>
      Promise.all([
        api.get<AinaRecord>(`/ainas/${encodeURIComponent(ainaId)}`),
        api.get<AinaInstallation[]>("/installations"),
      ]).then(([record, installations]) => {
        const runtime = record.manifest.runtime;
        return {
          tools: runtime.type === "package" ? runtime.tools : [],
          canvasTools: runtime.type === "package" ? (runtime.package?.canvas?.tools ?? []).map((tool) => tool.name) : [],
          installation: installations.find((item) => item.aina_id === ainaId) ?? null,
        };
      }),
    [ainaId],
  );
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    loaded.catch((loadError) => setError(apiErrorMessage(loadError)));
  }, [loaded]);

  const handle = useCallback(
    async (method: string, params: Record<string, unknown>): Promise<unknown> => {
      const { tools, installation } = await loaded;
      const specific = latest.current.extra?.(method, params);
      if (specific !== undefined) return specific;
      switch (method) {
        case "initialize":
          return { protocol: PROTOCOL, locale: i18n.language, theme: THEME, config: installation?.configuration ?? {}, ...latest.current.context };
        case "callTool": {
          const name = String(params.name ?? "");
          const args = isRecord(params.arguments) ? params.arguments : {};
          const tool = tools.find((item) => item.name === name);
          if (!tool || !tool.visibility.includes("app") || tool.status !== "active") {
            throw new BridgeError("PERMISSION_DENIED", t("ainaFrame.notCallable", { name }));
          }
          // Design §6.1: destructive calls are confirmed every time, writes once per opened frame.
          const ask = tool.effect === "destructive" || (tool.effect === "write" && !approvedWrites.current.has(name));
          const confirm = () =>
            new Promise<boolean>((resolve) => {
              pending.current = { tool, args, resolve };
              setConfirmation(pending.current);
            });
          if (ask && !(await confirm())) {
            throw new BridgeError("DENIED", t("ainaFrame.declined"));
          }
          if (tool.effect === "write") approvedWrites.current.add(name);
          return api.post(`/ainas/${encodeURIComponent(ainaId)}/app-tools/${encodeURIComponent(name)}`, {
            arguments: args,
            source,
            confirmed: tool.effect !== "read",
          });
        }
        case "sendPrompt": {
          const text = String(params.text ?? "").trim().slice(0, 4000);
          if (!text) throw new BridgeError("INVALID_REQUEST", "text is required");
          latest.current.onPrompt?.(text);
          return {};
        }
        default:
          throw new BridgeError("METHOD_NOT_FOUND", t("ainaFrame.unsupported", { method }));
      }
    },
    [ainaId, loaded, source, t],
  );

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      const target = frame.current?.contentWindow;
      if (!target || event.source !== target) return;
      if (origin && event.origin !== origin) return;
      const message = event.data as BridgeRequest | null;
      if (!message || message.unibot !== PROTOCOL || message.id === undefined) return;
      if (typeof message.method !== "string") {
        // The frame's answer to a request of the host.
        const waiting = hostRequests.current.get(String(message.id));
        if (!waiting) return;
        hostRequests.current.delete(String(message.id));
        const reply = message as BridgeReply;
        if (reply.error) waiting.reject(new BridgeError(reply.error.code ?? "TOOL_FAILED", reply.error.message ?? "failed"));
        else waiting.resolve(reply.result ?? {});
        return;
      }
      const reply = (body: Record<string, unknown>) =>
        target.postMessage({ unibot: PROTOCOL, jsonrpc: "2.0", id: message.id, ...body }, origin ?? "*");
      handle(message.method, isRecord(message.params) ? message.params : {}).then(
        (result) => reply({ result: result ?? {} }),
        (failure: unknown) =>
          reply({
            error: {
              code: failure instanceof BridgeError ? failure.code : "TOOL_FAILED",
              message: failure instanceof BridgeError ? failure.message : apiErrorMessage(failure),
            },
          }),
      );
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [frame, handle, origin]);

  const postEvent = useCallback(
    (type: string, data: unknown) => {
      frame.current?.contentWindow?.postMessage({ unibot: PROTOCOL, jsonrpc: "2.0", method: "event", params: { type, data } }, origin ?? "*");
    },
    [frame, origin],
  );

  /** Send a request to the frame and wait for its answer (design §9.5 canvas tool calls). */
  const requestFrame = useCallback(
    (method: string, params: Record<string, unknown>, timeoutMs: number) =>
      new Promise<unknown>((resolve, reject) => {
        const target = frame.current?.contentWindow;
        if (!target) {
          reject(new BridgeError("CANVAS_NOT_OPEN", "The canvas is not loaded"));
          return;
        }
        const id = `host-${nextHostRequest.current++}`;
        const timer = window.setTimeout(() => {
          hostRequests.current.delete(id);
          reject(new BridgeError("TIMEOUT", `The canvas did not answer ${method} in time`));
        }, timeoutMs);
        hostRequests.current.set(id, {
          resolve: (value) => {
            window.clearTimeout(timer);
            resolve(value);
          },
          reject: (error) => {
            window.clearTimeout(timer);
            reject(error);
          },
        });
        target.postMessage({ unibot: PROTOCOL, jsonrpc: "2.0", id, method, params }, origin ?? "*");
      }),
    [frame, origin],
  );

  const decide = useCallback((allowed: boolean) => {
    pending.current?.resolve(allowed);
    pending.current = null;
    setConfirmation(null);
  }, []);

  return { confirmation, decide, postEvent, requestFrame, loaded, error };
}

// --- Canvas tools (design §9.5) ------------------------------------------------------------------------------------

/** An open canvas that can run the canvas tools it registered. */
export type CanvasToolHost = {
  tools: Set<string>;
  invoke: (name: string, args: Record<string, unknown>, timeoutMs: number) => Promise<unknown>;
};

const canvasHosts = new Map<string, CanvasToolHost>();
const canvasHostListeners = new Set<() => void>();

/** Publish (or with null, withdraw) the open canvas of an AINA; called when it mounts and when it registers tools. */
export function setCanvasToolHost(ainaId: string, host: CanvasToolHost | null) {
  if (host) canvasHosts.set(ainaId, host);
  else canvasHosts.delete(ainaId);
  for (const listener of canvasHostListeners) listener();
}

/** The AINA's open canvas once it has registered ``name`` (with null: any tool); rejects after ``timeoutMs``. */
export function waitForCanvasTool(ainaId: string, name: string | null, timeoutMs: number): Promise<CanvasToolHost> {
  return new Promise((resolve, reject) => {
    const check = () => {
      const host = canvasHosts.get(ainaId);
      if (!host || (name === null ? host.tools.size === 0 : !host.tools.has(name))) return false;
      finish();
      resolve(host);
      return true;
    };
    const timer = window.setTimeout(() => {
      finish();
      reject(new BridgeError("CANVAS_NOT_READY", `The canvas did not register ${name ?? "its tools"} in time`));
    }, timeoutMs);
    const finish = () => {
      window.clearTimeout(timer);
      canvasHostListeners.delete(listener);
    };
    const listener = () => void check();
    canvasHostListeners.add(listener);
    check();
  });
}

/** The host's own confirmation for a tool call an AINA's UI requested; drawn outside the iframe. */
export function BridgeConfirmDialog({
  appName,
  confirmation,
  onDecide,
}: {
  appName: string;
  confirmation: Confirmation | null;
  onDecide: (allowed: boolean) => void;
}) {
  const { t } = useTranslation("canvas");
  if (!confirmation) return null;
  const destructive = confirmation.tool.effect === "destructive";
  return createPortal(
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/35 p-4" role="presentation">
      <div role="alertdialog" aria-modal="true" aria-label={t("ainaFrame.confirmTitle")} className="w-full max-w-sm rounded-xl border border-line bg-white p-4 shadow-xl">
        <div className="flex items-start gap-3">
          <span className={classNames("flex h-9 w-9 shrink-0 items-center justify-center rounded-lg", destructive ? "bg-danger-soft text-danger" : "bg-accent-soft text-accent")}>
            <ShieldAlert className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <h3 className="text-[13.5px] font-bold text-ink">{t("ainaFrame.confirmTitle")}</h3>
            <p className="mt-0.5 text-[12px] text-ink-muted">
              {t(destructive ? "ainaFrame.confirmDestructive" : "ainaFrame.confirmWrite", { app: appName, tool: confirmation.tool.title || confirmation.tool.name })}
            </p>
          </div>
        </div>
        <p className="mt-3 text-[11.5px] text-ink-muted">{confirmation.tool.description}</p>
        <pre className="mt-2 max-h-40 overflow-auto rounded-lg bg-app-soft p-2 font-mono text-[11px] text-ink">{JSON.stringify(confirmation.args, null, 2)}</pre>
        <div className="mt-3 flex justify-end gap-2">
          <button type="button" onClick={() => onDecide(false)} className="btn-outline">{t("ainaFrame.deny")}</button>
          <button type="button" autoFocus onClick={() => onDecide(true)} className={classNames("btn-primary", destructive && "bg-danger hover:bg-danger")}>{t("ainaFrame.allow")}</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
