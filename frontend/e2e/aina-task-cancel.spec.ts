import { expect, test, type Route } from "playwright/test";

// Long-running AINA tools (design §7.3): the user can cancel a background task from the list above the composer.
const CONVERSATION = "conv-tasks";
const NOW = "2026-10-18T04:00:00.000Z";

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

test("FE-E2E-TASK-01 a running background task can be cancelled", async ({ page }) => {
  let task = {
    id: "a".repeat(64), external_id: "t_1", aina_id: "com.example.expenses", conversation_id: CONVERSATION,
    tool_name: "monthly_report", title: "Spending report · 2026-09", status: "running", progress: 3, total: 10,
    message: "Reading receipts", result: null, error: null, created_at: NOW, updated_at: NOW,
  };
  const calls: string[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api/, "");
    const method = request.method();
    if (path === "/auth/config") return json(route, { auth_required: false, registration_enabled: true, github_enabled: false });
    if (method === "GET" && path === `/conversations/${CONVERSATION}`) {
      return json(route, {
        id: CONVERSATION, user_id: "anonymous", tenant_id: "default", workspace_id: null, title: "Report", category: "general",
        status: "active", run_status: "idle", config: {}, enabled_ainas: [], active_aina_ids: [], messages: [
          { id: "m1", role: "user", content: "Build my September report", content_type: "text", widgets: [], created_at: NOW },
        ], created_at: NOW, updated_at: NOW,
      });
    }
    if (method === "GET" && path === `/conversations/${CONVERSATION}/aina-tasks`) return json(route, [task]);
    if (method === "POST" && path === `/conversations/${CONVERSATION}/aina-tasks/${task.id}/cancel`) {
      calls.push("cancel");
      task = { ...task, status: "cancelled", message: null };
      return json(route, task);
    }
    if (method === "GET" && path.startsWith("/approvals")) return json(route, []);
    if (method === "GET" && path === "/notifications") return json(route, { items: [], unread: 0 });
    return json(route, { error: { message: `Unhandled ${method} ${path}` } }, 404);
  });

  await page.goto(`/chat/${CONVERSATION}`);
  const tasks = page.getByRole("region", { name: "后台任务" });
  await expect(tasks.getByText("Spending report · 2026-09")).toBeVisible();
  await expect(tasks.getByText("运行中 · 30%")).toBeVisible();
  await tasks.getByRole("button", { name: "取消 Spending report · 2026-09" }).click();

  await expect(tasks.getByText("已取消")).toBeVisible();
  await expect(tasks.getByRole("button", { name: /^取消/ })).toHaveCount(0);
  expect(calls).toEqual(["cancel"]);
});
