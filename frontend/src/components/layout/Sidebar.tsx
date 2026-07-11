import { useCallback, useEffect, useMemo, useState } from "react";
import { LayoutGrid, MessageSquarePlus, Search, Settings as SettingsIcon } from "lucide-react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { api, apiErrorMessage } from "@/lib/api";
import { classNames, timeAgo } from "@/lib/utils";
import type { ConversationRecord } from "@/types";

export const CONVERSATIONS_CHANGED_EVENT = "unibot:conversations-changed";

export function notifyConversationsChanged() {
  window.dispatchEvent(new Event(CONVERSATIONS_CHANGED_EVENT));
}

export function Sidebar() {
  const [conversations, setConversations] = useState<ConversationRecord[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const location = useLocation();

  const load = useCallback(async () => {
    try {
      const records = await api.get<ConversationRecord[]>(
        "/conversations?user_id=anonymous&tenant_id=default",
      );
      setConversations(records);
      setError(null);
    } catch (loadError) {
      setError(apiErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const refresh = () => void load();
    window.addEventListener(CONVERSATIONS_CHANGED_EVENT, refresh);
    return () => window.removeEventListener(CONVERSATIONS_CHANGED_EVENT, refresh);
  }, [load]);

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return conversations;
    return conversations.filter((conversation) => {
      const preview = conversation.messages.at(-1)?.content ?? "";
      return `${conversation.title} ${preview}`.toLowerCase().includes(normalized);
    });
  }, [conversations, query]);

  function startConversation() {
    if (location.pathname === "/chat") {
      window.dispatchEvent(new Event("unibot:new-conversation"));
    } else {
      navigate("/chat");
    }
  }

  return (
    <aside className="w-[248px] shrink-0 h-full bg-sidebar-bg text-ink-onDark flex flex-col dark-scroll">
      <div className="px-4 pt-4 pb-3">
        <Brand />
      </div>

      <div className="px-4 pb-2">
        <label className="flex items-center gap-2 h-9 rounded-lg px-2.5 bg-sidebar-bg border border-sidebar-border focus-within:border-accent">
          <Search className="w-3.5 h-3.5 text-ink-onDarkMuted" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            type="search"
            placeholder="搜索对话"
            aria-label="搜索对话"
            className="flex-1 min-w-0 bg-transparent text-[12.5px] placeholder:text-ink-onDarkMuted/70 text-ink-inverse outline-none"
          />
        </label>
      </div>

      <div className="px-4 pb-3">
        <button
          type="button"
          onClick={startConversation}
          className="w-full flex items-center justify-center gap-1.5 h-9 rounded-lg bg-accent hover:bg-accent-hover text-white text-[13px] font-semibold transition-colors"
        >
          <MessageSquarePlus className="w-4 h-4" />
          新建对话
        </button>
      </div>

      <div className="px-4 pb-2 flex items-center gap-2">
        <span className="text-[11px] font-bold uppercase tracking-[0.12em] text-ink-onDarkMuted">
          最近对话
        </span>
        <span className="ml-auto rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-ink-onDarkMuted">
          {conversations.length}
        </span>
      </div>

      <nav className="flex-1 min-h-0 overflow-y-auto px-3 pb-4 space-y-1.5" aria-label="对话列表">
        {loading ? <SkeletonList /> : null}
        {!loading && error ? (
          <div className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-[11.5px] text-red-200">
            <p>无法连接后端</p>
            <button type="button" onClick={() => void load()} className="mt-2 font-bold underline">
              重试
            </button>
          </div>
        ) : null}
        {!loading && !error && filtered.length === 0 ? (
          <div className="px-2 py-8 text-center text-[11.5px] text-ink-onDarkMuted">
            {query ? "没有匹配的对话" : "还没有对话"}
          </div>
        ) : null}
        {filtered.map((conversation) => (
          <ConversationLink key={conversation.id} conversation={conversation} />
        ))}
      </nav>

      <FooterUtility />
    </aside>
  );
}

function Brand() {
  return (
    <NavLink to="/chat" className="flex items-center gap-2.5" aria-label="Unibot 首页">
      <div className="w-8 h-8 rounded-lg bg-sidebar-active flex items-center justify-center shadow-soft">
        <Logo className="w-4 h-4 text-white" />
      </div>
      <div>
        <div className="text-ink-inverse text-[15px] font-extrabold tracking-tight">Unibot</div>
        <div className="text-[10px] text-ink-onDarkMuted">Agent Runtime</div>
      </div>
    </NavLink>
  );
}

function ConversationLink({ conversation }: { conversation: ConversationRecord }) {
  const preview = conversation.messages.at(-1)?.content || "等待第一条消息";
  return (
    <NavLink
      to={`/chat/${conversation.id}`}
      className={({ isActive }) =>
        classNames(
          "block rounded-lg px-3 py-2.5 border transition-colors",
          isActive
            ? "bg-sidebar-active border-transparent"
            : "bg-sidebar-bg border-sidebar-border hover:bg-sidebar-hover",
        )
      }
    >
      {({ isActive }) => (
        <>
          <div className="text-[12.5px] font-semibold text-ink-onDark truncate">
            {conversation.title}
          </div>
          <div
            className={classNames(
              "mt-1 text-[10.5px] truncate",
              isActive ? "text-white/75" : "text-ink-onDarkMuted/70",
            )}
          >
            {preview}
          </div>
          <div className="mt-1.5 flex items-center gap-1.5">
            <span className={classNames("w-1.5 h-1.5 rounded-full", isActive ? "bg-white" : "bg-success")} />
            <span className={classNames("text-[10px]", isActive ? "text-white/80" : "text-ink-onDarkMuted")}>
              {timeAgo(conversation.updated_at)}
            </span>
          </div>
        </>
      )}
    </NavLink>
  );
}

function SkeletonList() {
  return (
    <div className="space-y-2">
      {[0, 1, 2].map((item) => (
        <div key={item} className="rounded-lg p-3 border border-sidebar-border">
          <div className="h-3 w-28 rounded bg-white/5" />
          <div className="mt-2 h-2.5 w-36 rounded bg-white/5" />
        </div>
      ))}
    </div>
  );
}

function FooterUtility() {
  return (
    <div className="border-t border-sidebar-border p-3 grid grid-cols-2 gap-2">
      <FooterButton to="/apps" label="能力中心" icon={<LayoutGrid className="w-4 h-4" />} />
      <FooterButton to="/settings" label="运行中心" icon={<SettingsIcon className="w-4 h-4" />} />
    </div>
  );
}

function FooterButton({ to, label, icon }: { to: string; label: string; icon: React.ReactNode }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        classNames(
          "h-10 rounded-lg flex items-center justify-center gap-1.5 text-[11px] font-semibold transition-colors",
          isActive ? "bg-sidebar-active text-white" : "text-ink-onDark hover:bg-sidebar-hover",
        )
      }
    >
      {icon}
      {label}
    </NavLink>
  );
}

function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M4 18V6h16v3H8v2.5h10V14H8v4z" fill="currentColor" />
    </svg>
  );
}
