import { LockKeyhole, ShieldCheck } from "lucide-react";
import { Link, Outlet } from "react-router-dom";
import { useMockSession } from "@/lib/mockSession";

export function AdminRoute() {
  const { isAdmin } = useMockSession();
  return isAdmin ? <Outlet /> : <AdminAccessDenied />;
}

function AdminAccessDenied() {
  const { setRole } = useMockSession();

  return (
    <div className="flex h-full items-center justify-center bg-app-bg p-6">
      <section className="w-full max-w-md rounded-xl border border-line bg-white p-8 text-center shadow-card">
        <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-warning-soft text-warning">
          <LockKeyhole className="h-5 w-5" />
        </span>
        <p className="mt-5 text-[11px] font-bold uppercase tracking-[0.16em] text-warning-deep">403 · Mock 权限校验</p>
        <h1 className="mt-2 text-xl font-extrabold text-ink">需要管理员权限</h1>
        <p className="mt-2 text-sm leading-6 text-ink-muted">
          普通用户只能访问自己的对话和反馈入口，平台观测、反馈处理及运营数据仅对管理员展示。
        </p>
        <div className="mt-6 flex items-center justify-center gap-2">
          <Link to="/chat" className="btn-outline">返回聊天</Link>
          <button type="button" onClick={() => setRole("admin")} className="btn-primary">
            <ShieldCheck className="h-4 w-4" />切换为管理员
          </button>
        </div>
        <p className="mt-5 text-[11px] text-ink-subtle">当前为纯前端 Mock；正式权限仍需后端可信身份校验。</p>
      </section>
    </div>
  );
}
