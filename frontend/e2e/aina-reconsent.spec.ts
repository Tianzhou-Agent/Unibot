import { expect, test, type Route } from "playwright/test";

// AINA Protocol 2.0 upgrades (design §10): a paused installation shows what changed and is approved again through
// the install consent dialog.
const AINA = "com.example.expenses";
const NOW = "2026-10-17T04:00:00.000Z";

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

test("FE-E2E-AINA-07 a paused AINA lists what the upgrade changed and is approved again", async ({ page }) => {
  const installs: Record<string, unknown>[] = [];
  const pkg = {
    aina: { id: AINA, name: "Expenses", version: "2.0.0", publisher: { id: "example", name: "Example Inc." } },
    tools: { mcp: { url: "https://expenses.example.com/mcp", auth: { type: "none" } } },
    config: { schema: { type: "object", properties: { currency: { type: "string", title: "Default currency" } } } },
    permissions: [{ scope: "storage", optional: false }, { scope: "llm", reason: "Categorize receipts.", optional: true }],
  };
  const record = {
    manifest: {
      protocol_version: "2.0",
      aina: { id: AINA, name: "Expenses", version: "2.0.0", description: "Record expenses.", publisher: { id: "example", name: "Example Inc." } },
      runtime: {
        type: "package",
        archive_sha256: "0".repeat(64),
        package: pkg,
        tools: [{ name: "monthly_report", description: "Build a report.", effect: "read", visibility: ["model", "app"], status: "active" }],
      },
      capabilities: { skills: [], tools: [], ui: [], events: [] },
      permissions: ["storage", "llm"],
    },
    status: "registered",
    registered_at: NOW,
    last_health: {},
  };
  const paused = {
    aina_id: AINA, installed_version: "1.0.0", user_id: "anonymous", tenant_id: "default",
    granted_permissions: ["storage", "llm"], configuration: { currency: "EUR" }, status: "consent_required",
    installed_at: NOW, consent_changes: ["New tool 'monthly_report' (read)"],
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const apiPath = new URL(request.url()).pathname.replace(/^\/api/, "");
    if (apiPath === "/auth/config") return json(route, { auth_required: false, registration_enabled: true, github_enabled: false });
    if (apiPath === "/ainas") return json(route, [record]);
    if (apiPath === "/installations") return json(route, installs.length ? [{ ...paused, status: "active", consent_changes: [] }] : [paused]);
    if (request.method() === "POST" && apiPath === `/ainas/${AINA}/install`) {
      installs.push(request.postDataJSON());
      return json(route, { ...paused, status: "active", consent_changes: [] });
    }
    return json(route, { error: { message: `Unhandled ${apiPath}` } }, 404);
  });

  await page.goto(`/plugin/${AINA}`);
  const banner = page.getByRole("alert").filter({ hasText: "该应用已更新" });
  await expect(banner).toContainText("New tool 'monthly_report' (read)");
  await banner.getByRole("button", { name: "查看并确认" }).click();

  const dialog = page.getByRole("dialog");
  // The user's earlier choices are kept: the optional scope they granted and their settings.
  await expect(dialog.getByRole("checkbox", { name: /使用你的模型和额度/ })).toBeChecked();
  await expect(dialog.getByRole("textbox", { name: /Default currency/ })).toHaveValue("EUR");
  await dialog.getByRole("button", { name: "安装" }).click();

  await expect.poll(() => installs.length).toBe(1);
  expect(installs[0]).toMatchObject({ granted_permissions: ["storage", "llm"], configuration: { currency: "EUR" } });
  await expect(banner).toHaveCount(0);
});
