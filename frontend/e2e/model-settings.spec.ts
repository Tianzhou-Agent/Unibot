import { expect, test } from "playwright/test";

test("模型设置保留 Provider 配置且不再加载 MCP", async ({ page }) => {
  const requestedPaths: string[] = [];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api/, "");
    requestedPaths.push(path);
    let body: unknown = [];
    if (path === "/auth/config") {
      body = { auth_required: true, registration_enabled: false, github_enabled: false, huawei_enabled: false };
    } else if (path === "/auth/me") {
      body = { user: { id: "admin", email: "admin@example.com", name: "Admin", tenant_id: "default", providers: [], is_admin: true } };
    } else if (path === "/model-settings") {
      body = { providers: [], active_model: { source: "unconfigured", provider_id: null, model_id: null, model: null, model_name: null } };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/settings?tab=models");
  await expect(page.getByRole("heading", { name: "尚未添加 Provider", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "新增 Provider", exact: true })).toHaveCount(2);
  await expect(page.getByRole("heading", { name: "MCP Server", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "连接 Server", exact: true })).toHaveCount(0);
  expect(requestedPaths).toContain("/model-settings");
  expect(requestedPaths.some((path) => path.startsWith("/mcp-servers"))).toBe(false);
});
