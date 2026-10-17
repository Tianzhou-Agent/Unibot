import { useEffect, useRef } from "react";
import { api, apiUrl } from "@/lib/api";
import { waitForCanvasTool } from "@/components/widgets/ainaBridge";

// The shell's viewer channel for canvas tools (AINA Protocol 2.0 §9.5). While a conversation is shown, this tab keeps
// an SSE stream open and sends heartbeats saying whether the user is looking at it; the host sends each canvas call to
// the most recently visible tab only. A call opens the AINA's canvas if needed, waits for the canvas to register the
// tool, runs it in the canvas and posts the result back.
const HEARTBEAT_MS = 5000;
const REGISTER_TIMEOUT_MS = 10_000;

type CanvasCall = {
  call_id: string;
  aina_id: string;
  name: string;
  arguments: Record<string, unknown>;
  timeout_ms: number;
};

let tabClientId: string | null = null;

/** One id per browser tab, so the host can tell this tab's viewer from the user's other tabs. */
function clientId(): string {
  tabClientId ??= `tab_${crypto.randomUUID().replace(/-/g, "")}`;
  return tabClientId;
}

export function useCanvasViewer(conversationId: string | null, openCanvas: (ainaId: string) => void) {
  const openRef = useRef(openCanvas);
  openRef.current = openCanvas;

  useEffect(() => {
    if (!conversationId) return;
    const client = clientId();
    const base = `/conversations/${encodeURIComponent(conversationId)}`;
    const visible = () => document.visibilityState === "visible";
    const source = new EventSource(apiUrl(`${base}/canvas-viewer?client_id=${client}&visible=${visible()}`), { withCredentials: true });

    async function run(call: CanvasCall) {
      const deadline = Date.now() + call.timeout_ms - 500;
      // Confirm receipt at once: without it the host gives the call to another tab.
      const acknowledged = await api
        .post(`${base}/canvas-calls/${encodeURIComponent(call.call_id)}/ack`, { client_id: client, visible: visible() })
        .then(() => true, () => false);
      if (!acknowledged) return;
      let body: Record<string, unknown>;
      try {
        // Design decision C2: a closed canvas is opened, then the call runs.
        openRef.current(call.aina_id);
        const host = await waitForCanvasTool(call.aina_id, call.name, Math.max(0, Math.min(REGISTER_TIMEOUT_MS, deadline - Date.now())));
        body = { client_id: client, result: await host.invoke(call.name, call.arguments, Math.max(0, deadline - Date.now())) };
      } catch (error) {
        body = { client_id: client, error: { message: error instanceof Error ? error.message : String(error) } };
      }
      await api.post(`${base}/canvas-calls/${encodeURIComponent(call.call_id)}`, body).catch(() => undefined);
    }

    source.addEventListener("canvas.tool.requested", (event) => {
      void run(JSON.parse((event as MessageEvent<string>).data) as CanvasCall);
    });
    const beat = () => void api.post(`${base}/canvas-viewer/heartbeat`, { client_id: client, visible: visible() }).catch(() => undefined);
    const timer = window.setInterval(beat, HEARTBEAT_MS);
    document.addEventListener("visibilitychange", beat);
    return () => {
      source.close();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", beat);
    };
  }, [conversationId]);
}
