import { Activity, BarChart3, MessageSquareText } from "lucide-react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet } from "react-router-dom";
import { classNames } from "@/lib/utils";

const SECTIONS = [
  { to: "/admin/observability", key: "observability", icon: Activity },
  { to: "/admin/feedback", key: "feedback", icon: MessageSquareText },
  { to: "/admin/operations", key: "operations", icon: BarChart3 },
] as const;

export function AdminLayout() {
  const { t } = useTranslation("common");
  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-app-bg">
      <div className="flex shrink-0 flex-wrap items-center gap-1 border-b border-line bg-white px-3 py-2">
        {SECTIONS.map(({ to, key, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) => classNames(
              "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12px] font-bold transition-colors",
              isActive ? "bg-accent text-white shadow-sm" : "text-ink-muted hover:bg-app-soft hover:text-ink",
            )}
          >
            <Icon className="h-3.5 w-3.5" />{t(`admin.${key}`)}
          </NavLink>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-hidden">
        <Outlet />
      </div>
    </div>
  );
}
