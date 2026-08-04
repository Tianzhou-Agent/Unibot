import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import ChatModePage from "@/pages/ChatModePage";
import CanvasModePage from "@/pages/CanvasModePage";
import SettingsPage from "@/pages/SettingsPage";
import DebugPage from "@/pages/DebugPage";
import AllAppsPage from "@/pages/AllAppsPage";
import ScheduledAinaPage from "@/pages/ScheduledAinaPage";
import FeedbackAdminPage from "@/pages/FeedbackAdminPage";
import OperationsAnalyticsPage from "@/pages/OperationsAnalyticsPage";
import { AdminRoute } from "@/components/auth/AdminRoute";
import { AdminLayout } from "@/components/admin/AdminLayout";
import LoginPage from "@/pages/LoginPage";
import { RequireAuth } from "@/lib/auth";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth><AppShell /></RequireAuth>}>
        <Route index element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<ChatModePage />} />
        <Route path="/chat/:conversationId" element={<ChatModePage />} />
        <Route path="/canvas/:ainaId" element={<CanvasModePage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/apps" element={<AllAppsPage />} />
        <Route path="/schedules" element={<ScheduledAinaPage />} />
        <Route path="/obs" element={<DebugPage />} />
        <Route path="/debug" element={<LegacyDebugRedirect />} />
        <Route element={<AdminRoute />}>
          <Route path="/admin" element={<AdminLayout />}>
            <Route index element={<Navigate to="/admin/observability" replace />} />
            <Route path="observability" element={<DebugPage />} />
            <Route path="feedback" element={<FeedbackAdminPage />} />
            <Route path="operations" element={<OperationsAnalyticsPage />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Route>
    </Routes>
  );
}

function LegacyDebugRedirect() {
  const { search } = useLocation();
  return <Navigate to={`/obs${search}`} replace />;
}
