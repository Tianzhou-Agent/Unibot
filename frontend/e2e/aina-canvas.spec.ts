import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page, type Route } from "playwright/test";

// AINA Protocol 2.0 canvas (design §9): the Expenses example's real canvas files, served with the host's sandbox
// headers, talking to the shell over the UI bridge. The API is mocked; tool calls are recorded.
const AINA = "com.example.expenses";
const PACKAGE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../examples/expenses-aina/package");
const UI_HEADERS = {
  "Content-Security-Policy":
    "sandbox allow-scripts allow-forms allow-downloads; default-src 'self'; script-src 'self' 'unsafe-inline'; " +
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'none'; form-action 'none'; " +
    "base-uri 'none'; frame-ancestors http://127.0.0.1:5173",
  "X-Content-Type-Options": "nosniff",
  "Access-Control-Allow-Origin": "*",
};
const TOOLS = [
  { name: "add_expense", description: "Record one expense.", effect: "write", visibility: ["model", "app"], status: "active" },
  { name: "list_expenses", description: "List expenses.", effect: "read", visibility: ["model", "app"], status: "active" },
  { name: "delete_expense", description: "Delete one expense.", effect: "destructive", visibility: ["model", "app"], status: "active" },
  { name: "monthly_report", description: "Build a report.", effect: "read", visibility: ["model"], status: "active" },
];

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

type ToolCall = { tool: string; arguments: Record<string, unknown>; source: string; confirmed: boolean };

async function installApi(page: Page) {
  const calls: ToolCall[] = [];
  const expenses: { id: string; amount: number; category: string; note: string; day: string }[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const apiPath = url.pathname.replace(/^\/api/, "");
    const method = request.method();
    if (method === "GET" && apiPath === "/auth/config") {
      return json(route, { auth_required: false, registration_enabled: true, github_enabled: false });
    }
    if (method === "GET" && apiPath.startsWith(`/aina-ui/${AINA}/1.2.0/`)) {
      const file = apiPath.slice(`/aina-ui/${AINA}/1.2.0/`.length);
      const type = file.endsWith(".html") ? "text/html" : file.endsWith(".css") ? "text/css" : "text/javascript";
      return route.fulfill({ status: 200, contentType: `${type}; charset=utf-8`, headers: UI_HEADERS, body: readFileSync(path.join(PACKAGE, file)) });
    }
    if (method === "POST" && apiPath === `/ainas/${AINA}/open`) {
      return json(route, {
        aina_id: AINA,
        name: "Expenses",
        description: "Record expenses.",
        version: "1.2.0",
        conversation_id: null,
        route: `/chat?aina=${AINA}`,
        main_widget: {
          id: `${AINA}-canvas`,
          kind: "aina_canvas",
          title: "Expenses",
          description: "Record expenses.",
          fields: [],
          actions: [],
          apps: [],
          canvas: { kind: "package", version: "1.2.0", entry: "canvas/index.html", url: null },
        },
      });
    }
    if (method === "GET" && apiPath === `/ainas/${AINA}`) {
      return json(route, { manifest: { aina: { id: AINA, name: "Expenses", version: "1.2.0" }, runtime: { type: "package", tools: TOOLS } } });
    }
    if (method === "GET" && apiPath === "/installations") {
      return json(route, [{ aina_id: AINA, configuration: { currency: "EUR" }, granted_permissions: ["storage"], status: "active" }]);
    }
    const toolCall = apiPath.match(new RegExp(`^/ainas/${AINA.replace(/\./g, "\\.")}/app-tools/(\\w+)$`));
    if (method === "POST" && toolCall) {
      const payload = request.postDataJSON() as Omit<ToolCall, "tool">;
      calls.push({ tool: toolCall[1], ...payload });
      if (toolCall[1] === "add_expense") {
        const item = { id: `e_${expenses.length + 1}`, day: "2026-10-17", note: "", ...(payload.arguments as { amount: number; category: string; note?: string }) } as (typeof expenses)[number];
        expenses.push(item);
        return json(route, { structuredContent: { ...item, currency: "EUR" } });
      }
      if (toolCall[1] === "delete_expense") return json(route, { content: "Deleted" });
      return json(route, { structuredContent: { items: expenses, total: expenses.reduce((sum, item) => sum + item.amount, 0) } });
    }
    if (method === "GET" && apiPath.startsWith("/approvals")) return json(route, []);
    return json(route, { error: { message: `Unhandled ${method} ${apiPath}` } }, 404);
  });
  return calls;
}

test("FE-E2E-AINA-01 a package canvas calls tools through the host, which confirms every deletion", async ({ page }) => {
  const calls = await installApi(page);
  await page.goto(`/chat?aina=${AINA}`);
  const frame = page.frameLocator("iframe[sandbox]");
  await expect(frame.getByText("0.00 EUR in total")).toBeVisible();

  await frame.getByPlaceholder("Amount").fill("12.5");
  await frame.locator("select").selectOption("transport");
  await frame.getByPlaceholder("Note (optional)").fill("taxi");
  await frame.getByRole("button", { name: "Add" }).click();
  await expect(frame.getByRole("cell", { name: "taxi" })).toBeVisible();

  // Writes from the app's own UI are not asked.
  const dialog = page.getByRole("alertdialog");
  await frame.getByPlaceholder("Amount").fill("30");
  await frame.getByRole("button", { name: "Add" }).click();
  await expect(frame.locator("tbody tr")).toHaveCount(2);
  await expect(dialog).toHaveCount(0);

  // Destructive calls are asked every time; a declined call never reaches the API and the canvas is told.
  await frame.getByRole("button", { name: "Delete" }).first().click();
  await expect(dialog).toContainText("delete_expense");
  await dialog.getByRole("button", { name: "拒绝" }).click();
  await expect(frame.getByText("你拒绝了这次操作。")).toBeVisible();

  expect(calls.map((call) => [call.tool, call.confirmed, call.source])).toEqual([
    ["list_expenses", false, "canvas"],
    ["add_expense", false, "canvas"],
    ["list_expenses", false, "canvas"],
    ["add_expense", false, "canvas"],
    ["list_expenses", false, "canvas"],
  ]);
});

test("FE-E2E-AINA-02 the canvas is isolated and only its own window is answered", async ({ page }) => {
  const calls = await installApi(page);
  await page.goto(`/chat?aina=${AINA}`);
  await expect(page.frameLocator("iframe[sandbox]").getByText("0.00 EUR in total")).toBeVisible();
  const frame = page.frames().find((item) => item.url().includes("/aina-ui/"))!;

  expect(await frame.evaluate(() => self.origin)).toBe("null");
  expect(await frame.evaluate(() => { try { return document.cookie; } catch (error) { return (error as Error).name; } })).toBe("SecurityError");
  expect(await frame.evaluate(() => { try { return window.parent.document.title; } catch (error) { return (error as Error).name; } })).toBe("SecurityError");
  expect(await frame.evaluate(async () => { try { await fetch("/api/installations"); return "fetched"; } catch (error) { return (error as Error).name; } })).toBe("TypeError");
  // Model-only tools are refused by the host before any request.
  expect(await frame.evaluate(() => import("./aina-ui.js").then(({ host }) => host.callTool("monthly_report", {}).then(() => "called", (error: Error) => error.message)))).toContain("monthly_report");

  // A bridge request from any other window (here the shell page itself) is ignored.
  const before = calls.length;
  await page.evaluate(() => window.postMessage({ unibot: "aina-ui/2.0", jsonrpc: "2.0", id: 99, method: "callTool", params: { name: "delete_expense", arguments: { expense_id: "e_1" } } }, "*"));
  await page.waitForTimeout(300);
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  expect(calls.length).toBe(before);
});
