import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { CHAT_THREAD_SYSTEM_INTERACTION } from "@/mocks/seed";
import type { ChatThread } from "@/types";
import { AssistantMessage, Composer, UserMessage } from "@/components/chat/MessageBubble";
import { Topbar } from "@/components/layout/Topbar";

export default function ChatSystemInteractionPage() {
  const [thread, setThread] = useState<ChatThread>(CHAT_THREAD_SYSTEM_INTERACTION);
  const navigate = useNavigate();

  function handleConfirm(action: "confirm" | "cancel") {
    if (action === "confirm") navigate("/canvas");
  }

  useEffect(() => {
    api
      .get<ChatThread>("/sessions/sess_canvas_app/thread?kind=system")
      .then(setThread)
      .catch(() => {});
  }, []);

  return (
    <div className="h-full flex flex-col bg-app-bg">
      <Topbar title="系统交互状态" badge={{ label: "已就绪", tone: "success" }} />
      <div className="flex-1 min-h-0">
        <div className="h-full rounded-lg border border-line bg-white flex flex-col overflow-hidden">
          <div className="flex-1 min-h-0 overflow-y-auto px-1.5 py-2">
            <div className="max-w-[1648px] mx-auto space-y-2.5">
              {thread.messages.map((m) =>
                m.role === "user" ? (
                  <UserMessage key={m.id} content={m.content} files={m.files} />
                ) : (
                  <AssistantMessage key={m.id} message={m} onConfirm={handleConfirm} />
                ),
              )}
            </div>
          </div>
          <div className="border-t border-line bg-white px-4 py-3">
            <div className="max-w-[1648px] mx-auto">
              <Composer onSend={() => {}} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
