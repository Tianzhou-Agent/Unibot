import { useTranslation } from "react-i18next";
import { useCallback, useEffect, useRef, useState } from "react";
import { Bell, CheckCheck } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { openAina } from "@/lib/plugins";
import { classNames, timeAgo } from "@/lib/utils";

export interface NotificationItem {
  id: string;
  aina_id: string;
  aina_name: string;
  title: string;
  body: string;
  opens: "app" | "conversation";
  conversation_id?: string | null;
  created_at: string;
  read: boolean;
}

const POLL_MS = 30_000;
// Sidebar's CONVERSATIONS_CHANGED_EVENT (not imported: the sidebar imports this module). A finished chat turn may
// have sent a notification, so the inbox refreshes then too.
const CONVERSATIONS_CHANGED_EVENT = "unibot:conversations-changed";

/** The inbox of notifications AINAs send (Host API ``notify``): a bell with the unread count and a list. */
export function NotificationBell({ placement = "below", compact = false }: { placement?: "below" | "right"; compact?: boolean }) {
  const { t } = useTranslation("sidebar");
  const navigate = useNavigate();
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const inbox = await api.get<{ items: NotificationItem[]; unread: number }>("/notifications?limit=30");
      setItems(inbox.items);
      setUnread(inbox.unread);
    } catch {
      // The inbox is best effort; the next poll tries again.
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), POLL_MS);
    const onFocus = () => void load();
    window.addEventListener("focus", onFocus);
    window.addEventListener(CONVERSATIONS_CHANGED_EVENT, onFocus);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
      window.removeEventListener(CONVERSATIONS_CHANGED_EVENT, onFocus);
    };
  }, [load]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function openItem(item: NotificationItem) {
    setOpen(false);
    if (!item.read) {
      await api.post(`/notifications/${encodeURIComponent(item.id)}/read`).catch(() => undefined);
      void load();
    }
    if (item.opens === "conversation" && item.conversation_id) navigate(`/chat/${encodeURIComponent(item.conversation_id)}`);
    else navigate(await openAina(item.aina_id).catch(() => `/plugin/${encodeURIComponent(item.aina_id)}`));
  }

  async function markAllRead() {
    await api.post("/notifications/read-all").catch(() => undefined);
    await load();
  }

  const label = unread ? t("notifications.labelUnread", { count: unread }) : t("notifications.label");
  return (
    <div ref={box} className="relative">
      <button
        type="button"
        onClick={() => {
          setOpen((current) => !current);
          if (!open) void load();
        }}
        aria-label={label}
        title={label}
        aria-expanded={open}
        className={classNames(
          "relative flex items-center justify-center text-ink-subtle hover:bg-sidebar-hover hover:text-ink",
          compact ? "h-10 w-10 rounded-[10px]" : "h-7 w-7 rounded-lg",
        )}
      >
        <Bell className={compact ? "h-[19px] w-[19px]" : "h-4 w-4"} />
        {unread ? (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[9.5px] font-bold text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        ) : null}
      </button>
      {open ? (
        <section
          role="dialog"
          aria-label={t("notifications.title")}
          className={classNames(
            "absolute z-50 w-80 overflow-hidden rounded-xl border border-line-strong bg-white shadow-xl",
            placement === "below" ? "left-0 top-full mt-2" : "bottom-0 left-full ml-2",
          )}
        >
          <header className="flex items-center justify-between border-b border-line px-3.5 py-2.5">
            <h2 className="text-[13px] font-bold text-ink">{t("notifications.title")}</h2>
            {unread ? (
              <button type="button" onClick={() => void markAllRead()} className="flex items-center gap-1 text-[11.5px] text-accent hover:underline">
                <CheckCheck className="h-3.5 w-3.5" />{t("notifications.markAll")}
              </button>
            ) : null}
          </header>
          {items.length ? (
            <ul className="max-h-[420px] divide-y divide-line overflow-y-auto">
              {items.map((item) => (
                <li key={item.id}>
                  <button type="button" onClick={() => void openItem(item)} className="flex w-full gap-2.5 px-3.5 py-2.5 text-left hover:bg-app-soft">
                    <span className={classNames("mt-1.5 h-2 w-2 shrink-0 rounded-full", item.read ? "bg-transparent" : "bg-accent")} aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-baseline justify-between gap-2">
                        <span className="truncate text-[11px] font-semibold text-ink-muted">{item.aina_name}</span>
                        <span className="shrink-0 text-[10.5px] text-ink-subtle">{timeAgo(item.created_at)}</span>
                      </span>
                      <span className={classNames("block text-[12.5px] text-ink", !item.read && "font-bold")}>{item.title}</span>
                      {item.body ? <span className="mt-0.5 block whitespace-pre-line text-[11.5px] leading-5 text-ink-muted">{item.body}</span> : null}
                      <span className="sr-only">{item.read ? "" : t("notifications.unread")}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-3.5 py-6 text-center text-[12px] text-ink-muted">{t("notifications.empty")}</p>
          )}
        </section>
      ) : null}
    </div>
  );
}
