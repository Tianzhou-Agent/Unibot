import { useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useCanvasViewer } from "@/components/canvas/useCanvasViewer";
import { MainWidgetRenderer } from "@/components/widgets/MainWidgetRenderer";
import { HeadlessCanvasHost, waitForCanvasTool } from "@/components/widgets/ainaBridge";
import { api } from "@/lib/api";
import { useMockSession } from "@/lib/mockSession";
import type { AinaCanvasResponse, ConversationRecord } from "@/types";

// The platform's headless canvas host (AINA Protocol 2.0 §9.5). While no tab shows a conversation, the backend opens
// this page in a server-side headless browser, signed in as the conversation's owner, so canvas tools keep running
// after the user leaves. It shows only the conversation's canvases and joins as a background viewer once the canvas it
// was opened for has registered its tools; a tab the user opens takes the calls over.
const READY_TIMEOUT_MS = 45_000;

export default function CanvasHostPage() {
  const { conversationId = "" } = useParams<{ conversationId: string }>();
  const [search] = useSearchParams();
  const headlessClientId = search.get("client_id") ?? undefined;
  const firstAinaId = search.get("aina");
  const { profile } = useMockSession();
  const [conversation, setConversation] = useState<ConversationRecord | null>(null);
  const [ainaIds, setAinaIds] = useState<string[]>(firstAinaId ? [firstAinaId] : []);
  const [activeAinaId, setActiveAinaId] = useState<string | null>(firstAinaId);
  const [canvases, setCanvases] = useState<Record<string, AinaCanvasResponse>>({});
  const [ready, setReady] = useState(!firstAinaId);
  const opening = useRef(new Set<string>());

  useEffect(() => {
    api.get<ConversationRecord>(`/conversations/${encodeURIComponent(conversationId)}`)
      .then(setConversation)
      .catch((error) => console.error("canvas host: the conversation could not be loaded", error));
  }, [conversationId]);

  useEffect(() => {
    if (!conversation) return;
    for (const id of ainaIds) {
      if (opening.current.has(id)) continue;
      opening.current.add(id);
      api
        .post<AinaCanvasResponse>(`/ainas/${encodeURIComponent(id)}/open`, {
          user_id: profile.actorUserId,
          tenant_id: profile.tenantId,
          workspace_id: conversation.workspace_id ?? null,
          conversation_id: conversation.id,
        })
        .then((opened) => setCanvases((current) => ({ ...current, [id]: opened })))
        .catch((error) => console.error(`canvas host: ${id} could not be opened`, error));
    }
  }, [ainaIds, conversation, profile.actorUserId, profile.tenantId]);

  useEffect(() => {
    if (!firstAinaId) return;
    // Join once the canvas can take calls, or after a while anyway so the call reports what went wrong.
    void waitForCanvasTool(firstAinaId, null, READY_TIMEOUT_MS).catch(() => undefined).then(() => setReady(true));
  }, [firstAinaId]);

  useCanvasViewer(
    ready && conversation ? conversation.id : null,
    (id) => {
      setAinaIds((current) => (current.includes(id) ? current : [...current, id]));
      setActiveAinaId(id);
    },
    headlessClientId,
  );

  return (
    <HeadlessCanvasHost.Provider value>
      <div className="relative h-screen w-screen overflow-hidden bg-app-bg">
        {ainaIds.map((id) => {
          const opened = canvases[id];
          if (!opened) return null;
          return (
            <div key={id} className={id === activeAinaId ? "absolute inset-0" : "absolute inset-0 hidden"}>
              <MainWidgetRenderer
                ainaId={opened.aina_id}
                widget={opened.main_widget}
                workspaceId={conversation?.workspace_id ?? null}
                conversationId={conversation?.id ?? null}
              />
            </div>
          );
        })}
      </div>
    </HeadlessCanvasHost.Provider>
  );
}
