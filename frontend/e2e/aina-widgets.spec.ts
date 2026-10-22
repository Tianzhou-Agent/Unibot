import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page, type Route } from "playwright/test";

// AINA Protocol 2.0 widgets in the conversation (design §9.2), with the Expenses example's real widget files served
// under the host's sandbox headers. The API is mocked.
const AINA = "com.example.expenses";
const CONVERSATION = "conv_widgets";
const PACKAGE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../examples/expenses-aina/package");
const NOW = "2026-10-17T04:00:00.000Z";
const UI_HEADERS = {
  "Content-Security-Policy":
    "sandbox allow-scripts allow-forms allow-downloads; default-src 'self'; script-src 'self' 'unsafe-inline'; " +
    "style-src 'self' 'unsafe-inline'; connect-src 'none'; frame-ancestors http://127.0.0.1:5173",
  "Access-Control-Allow-Origin": "*",
};
const ADD_ARGS = { amount: 42.5, category: "food", day: "2026-10-01" };

function widget(purpose: "confirmation" | "result", extra: Record<string, unknown> = {}) {
  return {
    aina_id: AINA,
    version: "1.2.0",
    widget_id: purpose === "confirmation" ? "confirm-expense" : "expense-card",
    entry: purpose === "confirmation" ? "widgets/confirm-expense.html" : "widgets/expense-card.html",
    height: purpose === "confirmation" ? 160 : 120,
    purpose,
    tool_name: "add_expense",
    call_id: "add",
    arguments: ADD_ARGS,
    ...extra,
  };
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

function message(id: string, role: "user" | "assistant", content: string, widgets: unknown[] = []) {
  return { id, role, content, content_type: "text", widgets, created_at: NOW };
}

async function installApi(page: Page, { approval }: { approval: boolean }) {
  const confirmations: unknown[] = [];
  const resultWidget = {
    id: "aina-widget-add",
    kind: "aina_widget",
    title: "expense-card",
    description: "",
    fields: [],
    actions: [],
    apps: [],
    aina_widget: widget("result", { content: "", structured_content: { ...ADD_ARGS, id: "e_1", note: "", currency: "EUR" } }),
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const apiPath = new URL(request.url()).pathname.replace(/^\/api/, "");
    const method = request.method();
    if (method === "GET" && apiPath === "/auth/config") {
      return json(route, { auth_required: false, registration_enabled: true, github_enabled: false });
    }
    if (method === "GET" && apiPath.startsWith(`/aina-ui/${AINA}/1.2.0/`)) {
      const file = apiPath.slice(`/aina-ui/${AINA}/1.2.0/`.length);
      const type = file.endsWith(".html") ? "text/html" : file.endsWith(".css") ? "text/css" : "text/javascript";
      return route.fulfill({ status: 200, contentType: `${type}; charset=utf-8`, headers: UI_HEADERS, body: readFileSync(path.join(PACKAGE, file)) });
    }
    if (method === "GET" && apiPath === `/ainas/${AINA}`) {
      return json(route, { manifest: { aina: { id: AINA, name: "Expenses", version: "1.2.0" }, runtime: { type: "package", tools: [] } } });
    }
    if (method === "GET" && apiPath === "/installations") {
      return json(route, [{ aina_id: AINA, configuration: { currency: "EUR" }, granted_permissions: ["storage"], status: "active" }]);
    }
    if (method === "GET" && apiPath === `/conversations/${CONVERSATION}`) {
      return json(route, {
        id: CONVERSATION,
        user_id: "anonymous",
        tenant_id: "default",
        workspace_id: null,
        title: "Expenses",
        category: "general",
        status: "active",
        run_status: approval ? "approval_required" : "idle",
        config: {},
        enabled_ainas: [],
        active_aina_ids: [AINA],
        messages: approval
          ? [message("m1", "user", "I spent 42.5 on lunch")]
          : [message("m1", "user", "I spent 42.5 on lunch"), message("m2", "assistant", "Recorded.", [resultWidget])],
        created_at: NOW,
        updated_at: NOW,
      });
    }
    if (method === "GET" && apiPath === "/approvals") {
      if (!approval) return json(route, []);
      return json(route, [
        {
          id: "approval_1",
          conversation_id: CONVERSATION,
          user_id: "anonymous",
          tenant_id: "default",
          trace_id: "trace_1",
          tool_calls: [{ id: "add", type: "function", function: { name: "mcp_add", arguments: JSON.stringify(ADD_ARGS) } }],
          capability_names: ["add_expense"],
          status: "pending",
          created_at: NOW,
          widgets: [widget("confirmation")],
        },
      ]);
    }
    if (method === "POST" && apiPath === "/approvals/approval_1/confirm/stream") {
      confirmations.push(request.postDataJSON());
      const response = { conversation_id: CONVERSATION, content: "Recorded.", status: "completed", trace_id: "trace_1", iterations: 1, widgets: [] };
      return route.fulfill({
        contentType: "text/event-stream",
        body: `event: message.completed
data: ${JSON.stringify({ type: "message.completed", response })}

`,
      });
    }
    if (method === "POST" && apiPath === `/ainas/${AINA}/open`) {
      return json(route, {
        aina_id: AINA, name: "Expenses", description: "", version: "1.2.0", conversation_id: CONVERSATION,
        route: `/chat/${CONVERSATION}?aina=${AINA}`,
        main_widget: { id: `${AINA}-canvas`, kind: "panel", title: "Expenses", description: "", fields: [], actions: [], apps: [] },
      });
    }
    return json(route, { error: { message: `Unhandled ${method} ${apiPath}` } }, 404);
  });
  return confirmations;
}

test("FE-E2E-AINA-03 a confirmation widget proposes edited arguments and the host sends them on Allow", async ({ page }) => {
  const confirmations = await installApi(page, { approval: true });
  await page.goto(`/chat/${CONVERSATION}`);
  const card = page.getByRole("region", { name: "授权确认" });
  const frame = page.frameLocator('iframe[title="add_expense 组件"]');
  await expect(frame.locator("#preview")).toHaveText("42.50 EUR");

  await frame.getByLabel("Amount").fill("40");
  await frame.getByLabel("Amount").press("Tab");
  await frame.getByLabel("Category").selectOption("entertainment");
  await expect(card.getByText("已修改 · 将按显示的内容执行")).toBeVisible();
  await card.getByRole("button", { name: "允许" }).click();

  await expect.poll(() => confirmations.length).toBe(1);
  expect(confirmations[0]).toMatchObject({ edited_arguments: { add: { ...ADD_ARGS, amount: 40, category: "entertainment" } } });
});

test("FE-E2E-AINA-04 a result widget renders from the archived result, resizes and opens the canvas", async ({ page }) => {
  await installApi(page, { approval: false });
  await page.goto(`/chat/${CONVERSATION}`);
  const frame = page.frameLocator('iframe[title="add_expense 组件"]');
  await expect(frame.locator("#amount")).toHaveText("42.50 EUR");
  await expect(frame.locator("#detail")).toHaveText("food · 2026-10-01");
  await expect(page.locator('iframe[title="add_expense 组件"]')).toHaveAttribute("style", /height: 72px/);

  // A result widget cannot propose arguments: only confirmation widgets can.
  const inner = page.frames().find((item) => item.url().includes("expense-card.html"))!;
  const refused = await inner.evaluate(() =>
    import("./aina-ui.js").then(({ host }) => host.proposeArguments({ amount: 1 }).then(() => "accepted", (error: Error) => error.message)),
  );
  expect(refused).toBe("只有确认组件可以提议参数。");

  await frame.getByRole("button", { name: "Open Expenses" }).click();
  await expect(page).toHaveURL(new RegExp(`aina=${AINA.replace(/\./g, "\\.")}`));
});
