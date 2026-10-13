import { expect, test } from "playwright/test";

test("MCP 设置只提交凭据引用并展示发现的工具", async ({ page }) => {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api/, "");
    let body: unknown = [];
    if (path === "/auth/config") {
      body = { auth_required: true, registration_enabled: false, github_enabled: false, huawei_enabled: false };
    } else if (path === "/auth/me") {
      body = { user: { id: "admin", email: "admin@example.com", name: "Admin", tenant_id: "default", providers: [], is_admin: true } };
    } else if (path === "/model-settings") {
      body = { providers: [], active_model: { source: "unconfigured", provider_id: null, model_id: null, model: null, model_name: null } };
    } else if (path === "/mcp-servers" && request.method() === "POST") {
      const payload = request.postDataJSON();
      expect(payload).toMatchObject({
        server_id: "calculator", authentication: { type: "api_key", header_name: "X-API-Key" },
        credential_env: "MCP_CALCULATOR_TOKEN",
      });
      expect(payload.authentication).not.toHaveProperty("credential");
      body = { ...payload, status: "published", protocol_version: "2025-11-25", server_version: "1.0", tools: [{ name: "add", title: "Add numbers", description: "Add two numbers" }] };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/settings");
  await page.getByRole("button", { name: "模型", exact: true }).click();
  await page.getByRole("button", { name: "连接 Server", exact: true }).click();
  await page.getByLabel("Server ID", { exact: true }).fill("calculator");
  await page.getByLabel("名称", { exact: true }).fill("Calculator");
  await page.getByLabel("MCP 地址", { exact: true }).fill("https://mcp.example.com/mcp");
  await page.getByRole("combobox", { name: "认证方式", exact: true }).selectOption("api_key");
  await page.getByLabel("Header", { exact: true }).fill("X-API-Key");
  await page.getByLabel("环境变量或凭据引用名", { exact: true }).fill("MCP_CALCULATOR_TOKEN");
  await page.getByRole("button", { name: "连接并发现工具", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Calculator", exact: true })).toBeVisible();
  await expect(page.getByText("Add numbers", { exact: true })).toBeVisible();
});
