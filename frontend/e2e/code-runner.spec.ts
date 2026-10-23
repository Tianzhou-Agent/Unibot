import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "playwright/test";

const AINA = "unibot.code-runner";
const PACKAGE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../examples/code-runner-aina/package");

test("FE-E2E-009 explicitly installed Code Runner uses the package canvas and MCP bridge", async ({ page }) => {
  const calls: Record<string, unknown>[] = [];
  const tools = ["run_python", "run_bash", "run_node"].map((name) => ({ name, effect: "write", visibility: ["model", "app"], status: "active" }));
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const apiPath = new URL(request.url()).pathname.replace(/^\/api/, "");
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (apiPath === "/auth/config") return reply({ auth_required: false, registration_enabled: true });
    if (apiPath.startsWith(`/aina-ui/${AINA}/2.0.0/`)) {
      const file = apiPath.slice(`/aina-ui/${AINA}/2.0.0/`.length);
      return route.fulfill({
        contentType: file.endsWith(".html") ? "text/html" : "text/javascript",
        headers: { "Access-Control-Allow-Origin": "*" },
        body: readFileSync(path.join(PACKAGE, file)),
      });
    }
    if (apiPath === `/ainas/${AINA}/open`) return reply({
      aina_id: AINA, name: "Code Runner", version: "2.0.0", conversation_id: null,
      route: `/chat?aina=${AINA}`,
      main_widget: {
        id: `${AINA}-canvas`, kind: "aina_canvas", title: "Code Runner", fields: [], actions: [], apps: [],
        canvas: { kind: "package", version: "2.0.0", entry: "canvas/index.html", url: null },
      },
    });
    if (apiPath === `/ainas/${AINA}`) return reply({ manifest: {
      aina: { id: AINA, name: "Code Runner", version: "2.0.0" },
      runtime: { type: "package", bundled: false, tools },
    } });
    if (apiPath === "/installations") return reply([{ aina_id: AINA, status: "active", installed_version: "2.0.0", granted_permissions: [] }]);
    if (apiPath === `/ainas/${AINA}/app-tools/run_python`) {
      calls.push(request.postDataJSON().arguments);
      return reply({ structuredContent: { status: "succeeded", stdout: "42", stderr: "", exit_code: 0 } });
    }
    if (apiPath.startsWith("/approvals")) return reply([]);
    return reply({ error: { message: `Unexpected request ${apiPath}` } }, 404);
  });
  await page.goto(`/chat?aina=${AINA}`);
  const canvas = page.frameLocator('iframe[title="Code Runner"]');
  await expect(canvas.getByRole("heading", { name: "Code Runner" })).toBeVisible();
  await canvas.getByRole("textbox", { name: "Script", exact: true }).fill("print(42)");
  await canvas.getByRole("button", { name: "Run", exact: true }).click();
  // The run tools are writes, and writes from the app's own UI are not asked.
  await expect(canvas.getByRole("status")).toContainText("succeeded · exit 0");
  await expect(canvas.getByRole("status")).toContainText("42");
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  expect(calls).toEqual([{ script: "print(42)", timeout_seconds: 60, working_directory: "." }]);
});
