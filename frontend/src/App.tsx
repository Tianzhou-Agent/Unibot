import { Navigate, Route, Routes, useLocation, useParams, useSearchParams } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import ChatModePage from "@/pages/ChatModePage";
import SettingsPage from "@/pages/SettingsPage";
import DebugPage from "@/pages/DebugPage";
import AllAppsPage from "@/pages/AllAppsPage";
import ScheduledAinaPage from "@/pages/ScheduledAinaPage";
import FeedbackAdminPage from "@/pages/FeedbackAdminPage";
import OperationsAnalyticsPage from "@/pages/OperationsAnalyticsPage";
import { AdminRoute } from "@/components/auth/AdminRoute";
import { AdminLayout } from "@/components/admin/AdminLayout";
import LoginPage from "@/pages/LoginPage";
import WorkspacePage from "@/pages/WorkspacePage";
import FilesPage from "@/pages/FilesPage";
import { RequireAuth } from "@/lib/auth";
import { workspaceCanvasPath } from "@/lib/workspace";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth><AppShell /></RequireAuth>}>
        <Route index element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<ChatModePage />} />
        <Route path="/chat/:conversationId" element={<ChatModePage />} />
        <Route path="/canvas/:ainaId" element={<LegacyCanvasRedirect />} />
        <Route path="/files" element={<FilesPage />} />
        <Route path="/workspaces/:workspaceId" element={<WorkspacePage />} />
        <Route path="/workspaces/:workspaceId/chat" element={<ChatModePage />} />
        <Route path="/workspaces/:workspaceId/chat/:conversationId" element={<ChatModePage />} />
        <Route path="/workspaces/:workspaceId/canvas/:ainaId" element={<LegacyCanvasRedirect />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/plugin" element={<AllAppsPage />} />
        <Route path="/apps" element={<Navigate to="/plugin" replace />} />
        <Route path="/schedules" element={<ScheduledAinaPage />} />
        <Route path="/obs" element={<PersonalObsRoute />} />
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

// The personal overview lives in Settings; /obs only serves per-conversation drill-downs.
function PersonalObsRoute() {
  const [searchParams] = useSearchParams();
  if (!searchParams.get("sessionId")) return <Navigate to="/settings?tab=overview" replace />;
  return <DebugPage />;
}

function LegacyDebugRedirect() {
  const { search } = useLocation();
  return <Navigate to={`/obs${search}`} replace />;
}

// Canvases now open beside their conversation; keep old /canvas links working.
function LegacyCanvasRedirect() {
  const { workspaceId, ainaId = "" } = useParams<{ workspaceId?: string; ainaId: string }>();
  const [searchParams] = useSearchParams();
  return <Navigate to={workspaceCanvasPath(workspaceId, ainaId, searchParams.get("conversation"), searchParams.get("document"))} replace />;
}
