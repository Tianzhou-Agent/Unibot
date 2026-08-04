import { expect, test, type Page, type Route } from "playwright/test";

const USER = {
  id: "user-e2e",
  email: "owner@example.com",
  name: "端到端用户",
  avatar_url: null,
  tenant_id: "default",
  providers: ["password"],
};

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function installAuthApi(page: Page) {
  let authenticated = false;

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api/, "");
    const method = request.method();

    if (method === "GET" && path === "/auth/config") {
      return json(route, { auth_required: true, registration_enabled: true, github_enabled: true });
    }
    if (method === "GET" && path === "/auth/me") {
      return authenticated
        ? json(route, { user: USER })
        : json(route, { error: { user_message: "请先登录。" } }, 401);
    }
    if (method === "POST" && path === "/auth/register") {
      const payload = request.postDataJSON();
      expect(payload).toEqual({ name: USER.name, email: USER.email, password: "password-123" });
      authenticated = true;
      return json(route, { user: USER }, 201);
    }
    if (method === "POST" && path === "/auth/login") {
      const payload = request.postDataJSON();
      expect(payload).toEqual({ email: USER.email, password: "password-123" });
      authenticated = true;
      return json(route, { user: USER });
    }
    if (method === "POST" && path === "/auth/logout") {
      authenticated = false;
      return route.fulfill({ status: 204, body: "" });
    }
    if (method === "GET" && path === "/model-settings") {
      return json(route, {
        providers: [],
        active_model: { source: "unconfigured", provider_id: null, model_id: null, model: null, model_name: null },
      });
    }
    if (method === "GET") return json(route, []);
    return json(route, { error: { user_message: `未处理请求：${method} ${path}` } }, 501);
  });
}

test("用户可注册、退出并重新登录", async ({ page }) => {
  await installAuthApi(page);
  await page.goto("/chat");

  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "欢迎回来", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "使用 GitHub 登录", exact: true })).toHaveAttribute(
    "href",
    "/api/auth/github?next=%2Fchat",
  );

  await page.getByRole("button", { name: "注册", exact: true }).click();
  await page.getByRole("textbox", { name: "昵称", exact: true }).fill(USER.name);
  await page.getByRole("textbox", { name: "邮箱", exact: true }).fill(USER.email);
  await page.getByLabel("密码", { exact: true }).fill("password-123");
  await page.getByRole("button", { name: "注册并进入", exact: true }).click();

  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.getByText(USER.name, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);

  await page.getByRole("textbox", { name: "邮箱", exact: true }).fill(USER.email);
  await page.getByLabel("密码", { exact: true }).fill("password-123");
  await page.locator("form").getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.getByText(USER.email, { exact: true })).toBeVisible();
});
