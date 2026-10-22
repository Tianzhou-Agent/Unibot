import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import type { WidgetDefinition } from "@/types";
import {
  BridgeConfirmDialog,
  BridgeError,
  PACKAGE_SANDBOX,
  packageUiUrl,
  setCanvasToolHost,
  URL_SANDBOX,
  useAinaBridge,
  type CanvasToolHost,
} from "./ainaBridge";

/** An AINA 2.0 canvas (design §9.3) in the canvas pane: a packaged web app or the developer's own (URL canvas). */
export function AinaCanvasFrame({
  ainaId,
  widget,
  conversationId,
  refreshToken,
  onPrompt,
}: {
  ainaId: string;
  widget: WidgetDefinition;
  conversationId?: string | null;
  refreshToken?: string | null;
  onPrompt?: (prompt: string) => void;
}) {
  const { t } = useTranslation("canvas");
  const canvas = widget.canvas;
  const frame = useRef<HTMLIFrameElement>(null);
  const [title, setTitle] = useState(widget.title);
  const [notice, setNotice] = useState<string | null>(null);
  const src = canvas?.kind === "url" ? canvas.url ?? null : canvas ? packageUiUrl(ainaId, canvas.version, canvas.entry ?? "") : null;
  // A packaged canvas has an opaque origin, so replies can only be addressed to its window; a URL canvas is
  // checked and addressed by its own origin.
  const origin = canvas?.kind === "url" && canvas.url ? new URL(canvas.url).origin : null;
  // Canvas tools the canvas registered (design §9.5), and its latest summary for the agent.
  const registered = useMemo(() => new Set<string>(), []);
  const modelContext = useRef<string | null>(null);
  const conversationRef = useRef(conversationId);
  conversationRef.current = conversationId;

  const { confirmation, decide, postEvent, requestFrame, loaded, error } = useAinaBridge({
    ainaId,
    frame,
    origin,
    source: "canvas",
    context: { mode: "canvas", aina: { id: ainaId, version: canvas?.version } },
    onPrompt,
    extra: (method, params) => {
      switch (method) {
        case "setTitle":
          setTitle(String(params.text ?? "").slice(0, 120) || widget.title);
          return Promise.resolve({});
        case "notify":
          setNotice(String(params.text ?? "").slice(0, 300));
          return Promise.resolve({});
        case "registerTool": {
          const name = String(params.name ?? "");
          return loaded.then(({ canvasTools }) => {
            if (!canvasTools.includes(name)) throw new BridgeError("PERMISSION_DENIED", t("ainaFrame.undeclaredTool", { name }));
            registered.add(name);
            setCanvasToolHost(ainaId, host.current);
            return {};
          });
        }
        case "setModelContext":
          modelContext.current = String(params.value ?? "").slice(0, 4000);
          return sendModelContext();
        case "getIdentityToken":
          if (canvas?.kind !== "url") return Promise.reject(new BridgeError("PERMISSION_DENIED", t("ainaFrame.urlOnly")));
          return api.post(`/ainas/${encodeURIComponent(ainaId)}/identity-token`, {});
        default:
          return undefined;
      }
    },
  });

  function sendModelContext(): Promise<unknown> {
    const id = conversationRef.current;
    if (!id || modelContext.current === null) return Promise.resolve({});
    return api.put(`/conversations/${encodeURIComponent(id)}/canvas-context/${encodeURIComponent(ainaId)}`, { value: modelContext.current });
  }

  // Publish this canvas as the one that runs the AINA's canvas tools while it is open.
  const host = useRef<CanvasToolHost>({
    tools: registered,
    invoke: (name, args, timeoutMs) => requestFrame("invokeTool", { name, arguments: args }, timeoutMs),
    reload: () => undefined,
  });
  host.current.invoke = (name, args, timeoutMs) => requestFrame("invokeTool", { name, arguments: args }, timeoutMs);
  host.current.reload = () => {
    registered.clear();
    if (frame.current && src) frame.current.src = src;
  };
  useEffect(() => {
    setCanvasToolHost(ainaId, host.current);
    return () => setCanvasToolHost(ainaId, null);
  }, [ainaId]);

  // A canvas opened before its conversation existed sends its summary once there is one.
  useEffect(() => {
    if (conversationId) void sendModelContext().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  // Design §9.3: tell the canvas the agent may have changed data; for now after every finished chat run.
  const firstToken = useRef(refreshToken);
  useEffect(() => {
    if (refreshToken && refreshToken !== firstToken.current) postEvent("tool_result", {});
  }, [postEvent, refreshToken]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 5000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  if (!canvas || !src) return null;
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
      <BridgeConfirmDialog appName={widget.title} confirmation={confirmation} onDecide={decide} />
    </div>
  );
}
