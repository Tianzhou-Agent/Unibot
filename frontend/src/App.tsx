import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import ChatModePage from "@/pages/ChatModePage";
import TodoModePage from "@/pages/TodoModePage";
import ChatSystemInteractionPage from "@/pages/ChatSystemInteractionPage";
import CanvasModePage from "@/pages/CanvasModePage";
import SettingsPage from "@/pages/SettingsPage";
import AllAppsPage from "@/pages/AllAppsPage";
import MemoryAppPage from "@/pages/MemoryAppPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<ChatModePage />} />
        <Route path="/todo" element={<TodoModePage />} />
        <Route path="/system" element={<ChatSystemInteractionPage />} />
        <Route path="/canvas" element={<CanvasModePage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/apps" element={<AllAppsPage />} />
        <Route path="/apps/memory" element={<MemoryAppPage />} />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Route>
    </Routes>
  );
}
