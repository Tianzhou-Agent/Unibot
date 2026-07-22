import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import ChatModePage from "@/pages/ChatModePage";
import CanvasModePage from "@/pages/CanvasModePage";
import SettingsPage from "@/pages/SettingsPage";
import DebugPage from "@/pages/DebugPage";
import AllAppsPage from "@/pages/AllAppsPage";
import ScheduledAinaPage from "@/pages/ScheduledAinaPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<ChatModePage />} />
        <Route path="/chat/:conversationId" element={<ChatModePage />} />
        <Route path="/canvas/:ainaId" element={<CanvasModePage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/debug" element={<DebugPage />} />
        <Route path="/apps" element={<AllAppsPage />} />
        <Route path="/schedules" element={<ScheduledAinaPage />} />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Route>
    </Routes>
  );
}
