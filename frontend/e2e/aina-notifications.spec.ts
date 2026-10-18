import { expect, test, type Page, type Route } from "playwright/test";

// Notifications from AINAs (Host API ``notify``): the sidebar bell shows the unread count, and an entry opens the
// app or the conversation it came from. The API is mocked.
function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function installApi(page: Page) {
  const state = {
    calls: [] as string[],
    items: [
      { id: "ntf_2", aina_id: "com.example.expenses", aina_name: "Expenses", title: "September report ready", body: "Total 1,234.50 EUR", opens: "app", conversation_id: null, created_at: new Date().toISOString(), read: false },
      { id: "ntf_1", aina_id: "com.example.mail", aina_name: "Mail", title: "Reply drafted", body: "", opens: "conversation", conversation_id: "conv_42", created_at: new Date(Date.now() - 3_600_000).toISOString(), read: false },
    ],
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api/, "");
    const method = request.method();
    if (path === "/auth/config") return json(route, { auth_required: false, registration_enabled: true, github_enabled: false });
    if (method === "GET" && path === "/notifications") {
      return json(route, { items: state.items, unread: state.items.filter((item) => !item.read).length });
    }
    const read = path.match(/^\/notifications\/(.+)\/read$/);
    if (method === "POST" && read) {
      state.calls.push(`read ${read[1]}`);
      state.items = state.items.map((item) => (item.id === read[1] ? { ...item, read: true } : item));
      return route.fulfill({ status: 204 });
    }
    if (method === "POST" && path === "/notifications/read-all") {
      state.calls.push("read-all");
      state.items = state.items.map((item) => ({ ...item, read: true }));
      return route.fulfill({ status: 204 });
    }
    if (method === "POST" && path === "/ainas/com.example.expenses/open") {
      state.calls.push("open expenses");
      return json(route, { aina_id: "com.example.expenses", route: "/plugin/com.example.expenses", main_widget: null });
    }
    if (method === "GET" && ["/conversations", "/workspaces", "/ainas", "/installations"].some((prefix) => path.startsWith(prefix))) return json(route, []);
    return json(route, { error: { message: `Unhandled ${method} ${path}` } }, 404);
  });
  return state;
}

test("FE-E2E-NOTIFY-01 the bell counts unread notifications and opens what they point to", async ({ page }) => {
  const state = await installApi(page);
  await page.goto("/plugin");

  const bell = page.getByRole("button", { name: "通知，2 条未读" });
  await expect(bell).toBeVisible();
  await bell.click();
  const inbox = page.getByRole("dialog", { name: "通知" });
  await expect(inbox.getByText("September report ready")).toBeVisible();
  await expect(inbox.getByText("Total 1,234.50 EUR")).toBeVisible();
  await expect(inbox.getByText("Mail")).toBeVisible();

  // An app notification opens the app; a conversation notification opens its conversation.
  await inbox.getByRole("button", { name: /September report ready/ }).click();
  await expect(page).toHaveURL(/\/plugin\/com\.example\.expenses$/);
  await page.getByRole("button", { name: "通知，1 条未读" }).click();
  await page.getByRole("dialog", { name: "通知" }).getByRole("button", { name: /Reply drafted/ }).click();
  await expect(page).toHaveURL(/\/chat\/conv_42$/);
  expect(state.calls).toEqual(["read ntf_2", "open expenses", "read ntf_1"]);
  await expect(page.getByRole("button", { name: "通知", exact: true })).toBeVisible();
});

test("FE-E2E-NOTIFY-02 all notifications can be marked read at once", async ({ page }) => {
  const state = await installApi(page);
  await page.goto("/plugin");
  await page.getByRole("button", { name: "通知，2 条未读" }).click();
  await page.getByRole("dialog", { name: "通知" }).getByRole("button", { name: "全部标为已读" }).click();

  await expect(page.getByRole("button", { name: "通知", exact: true })).toBeVisible();
  expect(state.calls).toEqual(["read-all"]);
});
