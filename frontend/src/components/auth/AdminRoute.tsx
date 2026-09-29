import { LockKeyhole, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link, Outlet } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { useMockSession } from "@/lib/mockSession";

export function AdminRoute() {
  const { user, config } = useAuth();
  const { isAdmin: mockIsAdmin } = useMockSession();
  const isAdmin = config.auth_required ? Boolean(user?.is_admin) : mockIsAdmin;
  return isAdmin ? <Outlet /> : <AdminAccessDenied allowMockSwitch={!config.auth_required} />;
}

function AdminAccessDenied({ allowMockSwitch }: { allowMockSwitch: boolean }) {
  const { setRole } = useMockSession();
  const { t } = useTranslation("common");

  return (
    <div className="flex h-full items-center justify-center bg-app-bg p-6">
      <section className="w-full max-w-md rounded-xl border border-line bg-white p-8 text-center shadow-card">
        <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-warning-soft text-warning">
          <LockKeyhole className="h-5 w-5" />
        </span>
        <p className="mt-5 text-[11px] font-bold uppercase tracking-[0.16em] text-warning-deep">{t("forbidden.eyebrow")}</p>
        <h1 className="mt-2 text-xl font-extrabold text-ink">{t("forbidden.title")}</h1>
        <p className="mt-2 text-sm leading-6 text-ink-muted">
          {t("forbidden.body")}
        </p>
        <div className="mt-6 flex items-center justify-center gap-2">
          <Link to="/chat" className="btn-outline">{t("forbidden.back")}</Link>
          {allowMockSwitch ? (
            <button type="button" onClick={() => setRole("admin")} className="btn-primary">
              <ShieldCheck className="h-4 w-4" />{t("forbidden.switch")}
            </button>
          ) : null}
        </div>
        <p className="mt-5 text-[11px] text-ink-subtle">
          {allowMockSwitch ? t("forbidden.mockHint") : t("forbidden.realHint")}
        </p>
      </section>
    </div>
  );
}
