import { useCallback, useEffect, useState } from "react";
import { Maximize2, Minimize2, X } from "lucide-react";
import { PersonalObservabilityView } from "@/components/observability/PersonalObservabilityView";
import { api, apiErrorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useMockSession } from "@/lib/mockSession";
import { loadAllPersonalLlmCalls } from "@/lib/obsData";
import { classNames } from "@/lib/utils";
import type { ConversationRecord, LLMCallRecord, TraceRecord } from "@/types";

export function ConversationObsDrawer({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const { config } = useAuth();
  const { profile } = useMockSession();
  const [mounted, setMounted] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [conversations, setConversations] = useState<ConversationRecord[]>([]);
  const [traces, setTraces] = useState<TraceRecord[]>([]);
  const [llmCalls, setLlmCalls] = useState<LLMCallRecord[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setMounted(true);
  }, []);

  const load = useCallback(async () => {
    const actorQuery = config.auth_required
      ? ""
      : `tenant_id=${encodeURIComponent(profile.tenantId)}&user_id=${encodeURIComponent(profile.actorUserId)}`;
    const querySuffix = actorQuery ? `?${actorQuery}` : "";
    try {
      const [traceData, llmCallData, conversationData] = await Promise.all([
        api.get<TraceRecord[]>(`/traces${querySuffix}`),
        loadAllPersonalLlmCalls(actorQuery),
        api.get<ConversationRecord[]>(`/conversations${querySuffix}`),
      ]);
      setTraces(traceData);
      setLlmCalls(llmCallData);
      setConversations(conversationData);
      setError(null);
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    }
  }, [config.auth_required, profile.actorUserId, profile.tenantId]);

  useEffect(() => {
    void load();
  }, [load, sessionId]);

  return (
    <div className="fixed inset-0 z-50">
      <div
        className={classNames("absolute inset-0 bg-black/30 transition-opacity duration-300", mounted ? "opacity-100" : "opacity-0")}
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        role="dialog"
        aria-label="对话观测抽屉"
        className={classNames(
          "absolute inset-y-0 right-0 flex w-full flex-col bg-app-bg shadow-2xl transition-[width,transform] duration-300",
          expanded ? "inset-x-0" : "max-w-[960px]",
          mounted ? "translate-x-0" : "translate-x-full",
        )}
      >
        <div className="flex shrink-0 items-center gap-2 border-b border-line bg-white px-3 py-2">
          <span className="text-[13px] font-extrabold text-ink">对话观测</span>
          <span className="truncate font-mono text-[10.5px] text-ink-subtle">Session ID：{sessionId}</span>
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            aria-label={expanded ? "退出全屏" : "展开全屏"}
            className="ml-auto flex h-7 w-7 items-center justify-center rounded-md text-ink-muted transition-colors hover:bg-app-soft hover:text-ink"
          >
            {expanded ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭观测抽屉"
            className="flex h-7 w-7 items-center justify-center rounded-md text-ink-muted transition-colors hover:bg-app-soft hover:text-ink"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1">
          <PersonalObservabilityView
            error={error}
            sessionId={sessionId}
            conversations={conversations}
            traces={traces}
            llmCalls={llmCalls}
            urlParams={false}
          />
        </div>
      </div>
    </div>
  );
}
