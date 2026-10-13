import { expect, test, type Page, type Route } from "playwright/test";

const USER = {
  id: "user-e2e",
  email: "owner@example.com",
  name: "端到端用户",
  avatar_url: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E",
  tenant_id: "default",
  providers: ["password"],
};

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function installAuthApi(page: Page, huaweiEnabled = true, oidcEnabled = true) {
  let authenticated = false;

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api/, "");
    const method = request.method();

    if (method === "GET" && path === "/auth/config") {
      return json(route, {
        auth_required: true, registration_enabled: true, github_enabled: true, huawei_enabled: huaweiEnabled,
        google_enabled: oidcEnabled, microsoft_enabled: oidcEnabled, apple_enabled: oidcEnabled,
      });
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
  await expect(page.getByRole("link", { name: "使用华为账号登录", exact: true })).toHaveAttribute(
    "href",
    "/api/auth/huawei?next=%2Fchat",
  );
  for (const [provider, label] of [["google", "Google"], ["microsoft", "Microsoft"], ["apple", "Apple"]]) {
    await expect(page.getByRole("link", { name: `使用 ${label} 登录`, exact: true })).toHaveAttribute(
      "href", `/api/auth/${provider}?next=%2Fchat`,
    );
  }

  await page.getByRole("button", { name: "注册", exact: true }).click();
  await page.getByRole("textbox", { name: "昵称", exact: true }).fill(USER.name);
  await page.getByRole("textbox", { name: "邮箱", exact: true }).fill(USER.email);
  await page.getByLabel("密码", { exact: true }).fill("password-123");
  await page.getByRole("button", { name: "注册并进入", exact: true }).click();

  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.getByText(USER.name, { exact: true })).toBeVisible();
  await expect(page.locator("aside").getByText(USER.name, { exact: true }).locator("..").locator("..").locator("img")).toHaveAttribute("src", USER.avatar_url);
  await page.getByRole("button", { name: "收起导航", exact: true }).click();
  const compactNavigation = page.getByRole("complementary", { name: "快捷导航" });
  const compactAccount = compactNavigation.getByRole("button", { name: `当前用户 ${USER.name}`, exact: true });
  await expect(compactAccount.locator("img")).toHaveAttribute("src", USER.avatar_url);
  await expect(compactNavigation.getByText("林", { exact: true })).toHaveCount(0);
  await expect(compactNavigation.getByText("周", { exact: true })).toHaveCount(0);
  await compactNavigation.getByRole("button", { name: "展开导航", exact: true }).click();
  await page.locator("aside").getByRole("link", { name: "设置", exact: true }).last().click();
  await page.getByRole("button", { name: "账户", exact: true }).click();
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);

  await page.getByRole("textbox", { name: "邮箱", exact: true }).fill(USER.email);
  await page.getByLabel("密码", { exact: true }).fill("password-123");
  await page.locator("form").getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.getByText(USER.email, { exact: true })).toBeVisible();
});

test("未配置华为 OAuth 时不显示登录入口", async ({ page }) => {
  await installAuthApi(page, false);
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("link", { name: "使用华为账号登录", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "使用 GitHub 登录", exact: true })).toBeVisible();
});

test("未配置 Google、Microsoft、Apple 时不显示入口", async ({ page }) => {
  await installAuthApi(page, true, false);
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/login$/);
  for (const label of ["Google", "Microsoft", "Apple"]) {
    await expect(page.getByRole("link", { name: `使用 ${label} 登录`, exact: true })).toHaveCount(0);
  }
  await expect(page.getByRole("link", { name: "使用华为账号登录", exact: true })).toBeVisible();
});

test("第三方登录入口保留原页面并支持英文", async ({ page }) => {
  await installAuthApi(page);
  await page.goto("/apps?tab=mine");
  await expect(page).toHaveURL(/\/login$/);
  await page.getByRole("combobox", { name: "语言", exact: true }).selectOption("en");
  for (const [provider, label] of [["google", "Google"], ["microsoft", "Microsoft"], ["apple", "Apple"]]) {
    await expect(page.getByRole("link", { name: `Sign in with ${label}`, exact: true })).toHaveAttribute(
      "href", `/api/auth/${provider}?next=%2Fapps%3Ftab%3Dmine`,
    );
  }
});
