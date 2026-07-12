import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import ChatModePage from "@/pages/ChatModePage";
import CanvasModePage from "@/pages/CanvasModePage";
import SettingsPage from "@/pages/SettingsPage";
import AllAppsPage from "@/pages/AllAppsPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<ChatModePage />} />
        <Route path="/chat/:conversationId" element={<ChatModePage />} />
        <Route path="/canvas/:ainaId" element={<CanvasModePage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/apps" element={<AllAppsPage />} />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Route>
    </Routes>
  );
}
