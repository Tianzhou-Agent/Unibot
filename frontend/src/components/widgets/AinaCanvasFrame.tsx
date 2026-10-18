import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ShieldAlert } from "lucide-react";
import i18n from "@/i18n";
import { api, apiErrorMessage, apiUrl } from "@/lib/api";
import { classNames } from "@/lib/utils";
import type { AinaInstallation, AinaRecord, WidgetDefinition } from "@/types";

// The UI bridge of AINA Protocol 2.0 (design §9.4), host side. The AINA's canvas runs in a sandboxed iframe and can
// only ask; this component decides. It answers messages from that iframe alone, relays app-visible tool calls only,
// and asks the user itself before any call that changes data.
const PROTOCOL = "aina-ui/2.0";
const PACKAGE_SANDBOX = "allow-scripts allow-forms allow-downloads";
const URL_SANDBOX = "allow-scripts allow-same-origin allow-forms allow-popups allow-downloads";
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

type PackageTool = Extract<AinaRecord["manifest"]["runtime"], { type: "package" }>["tools"][number];

type BridgeRequest = { unibot: string; id: number | string; method: string; params?: Record<string, unknown> };

class BridgeError extends Error {
  constructor(readonly code: string, message: string) {
    super(message);
  }
}

type Confirmation = {
  tool: PackageTool;
  args: Record<string, unknown>;
  resolve: (allowed: boolean) => void;
};

export function AinaCanvasFrame({
  ainaId,
  widget,
  refreshToken,
  onPrompt,
}: {
  ainaId: string;
  widget: WidgetDefinition;
  refreshToken?: string | null;
  onPrompt?: (prompt: string) => void;
}) {
  const { t } = useTranslation("canvas");
  const canvas = widget.canvas;
  const frame = useRef<HTMLIFrameElement>(null);
  const [title, setTitle] = useState(widget.title);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const approvedWrites = useRef(new Set<string>());
  const onPromptRef = useRef(onPrompt);
  onPromptRef.current = onPrompt;

  // What the bridge needs before it answers: the reviewed tools and this user's (non-secret) configuration.
  const loaded = useMemo(
    () =>
      Promise.all([
        api.get<AinaRecord>(`/ainas/${encodeURIComponent(ainaId)}`),
        api.get<AinaInstallation[]>("/installations"),
      ]).then(([record, installations]) => ({
        tools: record.manifest.runtime.type === "package" ? record.manifest.runtime.tools : [],
        installation: installations.find((item) => item.aina_id === ainaId) ?? null,
      })),
    [ainaId],
  );
  useEffect(() => {
    loaded.catch((loadError) => setError(apiErrorMessage(loadError)));
  }, [loaded]);

  const src = useMemo(() => {
    if (!canvas) return null;
    if (canvas.kind === "url") return canvas.url ?? null;
    const entry = (canvas.entry ?? "").split("/").map(encodeURIComponent).join("/");
    return apiUrl(`/aina-ui/${encodeURIComponent(ainaId)}/${encodeURIComponent(canvas.version)}/${entry}`);
  }, [ainaId, canvas]);
  // A packaged canvas has an opaque origin, so replies can only be addressed to its window; a URL canvas is
  // checked and addressed by its own origin.
  const origin = canvas?.kind === "url" && canvas.url ? new URL(canvas.url).origin : null;

  const ask = useCallback(
    (tool: PackageTool, args: Record<string, unknown>) =>
      new Promise<boolean>((resolve) => setConfirmation({ tool, args, resolve })),
    [],
  );

  const handle = useCallback(
    async (method: string, params: Record<string, unknown>): Promise<unknown> => {
      const { tools, installation } = await loaded;
      switch (method) {
        case "initialize":
          return {
            protocol: PROTOCOL,
            mode: "canvas",
            aina: { id: ainaId, version: canvas?.version },
            locale: i18n.language,
            theme: THEME,
            config: installation?.configuration ?? {},
          };
        case "callTool": {
          const name = String(params.name ?? "");
          const args = isRecord(params.arguments) ? params.arguments : {};
          const tool = tools.find((item) => item.name === name);
          if (!tool || !tool.visibility.includes("app") || tool.status !== "active") {
            throw new BridgeError("PERMISSION_DENIED", t("ainaFrame.notCallable", { name }));
          }
          // Design §6.1: destructive calls are confirmed every time, writes once per opened canvas.
          const needsConfirmation = tool.effect === "destructive" || (tool.effect === "write" && !approvedWrites.current.has(name));
          if (needsConfirmation && !(await ask(tool, args))) {
            throw new BridgeError("DENIED", t("ainaFrame.declined"));
          }
          if (tool.effect === "write") approvedWrites.current.add(name);
          return api.post(`/ainas/${encodeURIComponent(ainaId)}/app-tools/${encodeURIComponent(name)}`, {
            arguments: args,
            source: "canvas",
            confirmed: tool.effect !== "read",
          });
        }
        case "sendPrompt": {
          const text = String(params.text ?? "").trim().slice(0, 4000);
          if (!text) throw new BridgeError("INVALID_REQUEST", "text is required");
          onPromptRef.current?.(text);
          return {};
        }
        case "setTitle":
          setTitle(String(params.text ?? "").slice(0, 120) || widget.title);
          return {};
        case "notify":
          setNotice(String(params.text ?? "").slice(0, 300));
          return {};
        case "getIdentityToken":
          if (canvas?.kind !== "url") throw new BridgeError("PERMISSION_DENIED", t("ainaFrame.urlOnly"));
          return api.post(`/ainas/${encodeURIComponent(ainaId)}/identity-token`, {});
        default:
          throw new BridgeError("METHOD_NOT_FOUND", t("ainaFrame.unsupported", { method }));
      }
    },
    [ainaId, ask, canvas, loaded, t, widget.title],
  );

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      const target = frame.current?.contentWindow;
      if (!target || event.source !== target) return;
      if (origin && event.origin !== origin) return;
      const message = event.data as BridgeRequest | null;
      if (!message || message.unibot !== PROTOCOL || typeof message.method !== "string" || message.id === undefined) return;
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
  }, [handle, origin]);

  // Design §9.3: tell the canvas the agent may have changed data; for now after every finished chat run.
  const firstToken = useRef(refreshToken);
  useEffect(() => {
    if (!refreshToken || refreshToken === firstToken.current) return;
    frame.current?.contentWindow?.postMessage(
      { unibot: PROTOCOL, jsonrpc: "2.0", method: "event", params: { type: "tool_result", data: {} } },
      origin ?? "*",
    );
  }, [origin, refreshToken]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 5000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  if (!canvas || !src) return null;

  function decide(allowed: boolean) {
    confirmation?.resolve(allowed);
    setConfirmation(null);
  }

  return (
    <div className="relative flex h-full flex-col bg-white">
      {error ? <p role="alert" className="m-3 rounded-lg bg-danger-soft px-3 py-2 text-[12px] text-danger-deep">{error}</p> : null}
      {notice ? <p role="status" className="absolute right-3 top-3 z-10 max-w-xs rounded-lg bg-ink px-3 py-2 text-[12px] text-white shadow-lg">{notice}</p> : null}
      <iframe
        ref={frame}
        title={title}
        src={src}
        sandbox={canvas.kind === "url" ? URL_SANDBOX : PACKAGE_SANDBOX}
        referrerPolicy="no-referrer"
        className="min-h-0 w-full flex-1 border-0"
      />
      {confirmation ? (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-slate-950/35 p-4" role="presentation">
          <div role="alertdialog" aria-modal="true" aria-label={t("ainaFrame.confirmTitle")} className="w-full max-w-sm rounded-xl border border-line bg-white p-4 shadow-xl">
            <div className="flex items-start gap-3">
              <span className={classNames("flex h-9 w-9 shrink-0 items-center justify-center rounded-lg", confirmation.tool.effect === "destructive" ? "bg-danger-soft text-danger" : "bg-accent-soft text-accent")}>
                <ShieldAlert className="h-4 w-4" />
              </span>
              <div className="min-w-0">
                <h3 className="text-[13.5px] font-bold text-ink">{t("ainaFrame.confirmTitle")}</h3>
                <p className="mt-0.5 text-[12px] text-ink-muted">
                  {t(confirmation.tool.effect === "destructive" ? "ainaFrame.confirmDestructive" : "ainaFrame.confirmWrite", { app: widget.title, tool: confirmation.tool.title || confirmation.tool.name })}
                </p>
              </div>
            </div>
            <p className="mt-3 text-[11.5px] text-ink-muted">{confirmation.tool.description}</p>
            <pre className="mt-2 max-h-40 overflow-auto rounded-lg bg-app-soft p-2 font-mono text-[11px] text-ink">{JSON.stringify(confirmation.args, null, 2)}</pre>
            <div className="mt-3 flex justify-end gap-2">
              <button type="button" onClick={() => decide(false)} className="btn-outline">{t("ainaFrame.deny")}</button>
              <button type="button" autoFocus onClick={() => decide(true)} className={classNames("btn-primary", confirmation.tool.effect === "destructive" && "bg-danger hover:bg-danger")}>{t("ainaFrame.allow")}</button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
