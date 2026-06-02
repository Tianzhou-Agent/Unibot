import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { CHAT_THREAD_TODO_MODE } from "@/mocks/seed";
import type { ChatThread } from "@/types";
import { AssistantMessage, Composer, UserMessage } from "@/components/chat/MessageBubble";
import { Topbar } from "@/components/layout/Topbar";

export default function TodoModePage() {
  const [thread, setThread] = useState<ChatThread>(CHAT_THREAD_TODO_MODE);

  useEffect(() => {
    api.get<ChatThread>("/sessions/sess_sr_add/thread").then(setThread).catch(() => {});
  }, []);

  return (
    <div className="h-full flex flex-col bg-app-bg">
      <Topbar title={thread.title} badge={{ label: "任务进行中", tone: "warning" }} />
      <div className="flex-1 min-h-0">
        <div className="h-full rounded-lg border border-line bg-white flex flex-col overflow-hidden">
          <div className="flex-1 min-h-0 overflow-y-auto px-1.5 py-2">
            <div className="max-w-[1648px] mx-auto space-y-2.5">
              {thread.messages.map((m) =>
                m.role === "user" ? (
                  <UserMessage key={m.id} content={m.content} files={m.files} />
                ) : (
                  <AssistantMessage key={m.id} message={m} onChoice={() => {}} />
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
