import { expect, test } from "playwright/test";

test("context root is retained across deep links, login, API requests and refresh", async ({ page, baseURL }) => {
  const basePath = new URL(baseURL!).pathname;
  let authenticated = false;
  const user = { id: "base-path-user", email: "owner@example.com", name: "Context root user", tenant_id: "default", providers: ["password"], is_admin: false };
  const apiPaths: string[] = [];
  await page.route("**/api/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    apiPaths.push(pathname);
    expect(pathname.startsWith(`${basePath}api/`)).toBeTruthy();
    const path = pathname.slice(`${basePath}api`.length);
    let body: unknown = [];
    let status = 200;
    if (path === "/auth/config") body = {
      auth_required: true, default_language: "en", registration_enabled: false,
      github_enabled: true, huawei_enabled: true, google_enabled: true, microsoft_enabled: true, apple_enabled: true,
    };
    if (path === "/auth/me") {
      body = authenticated ? { user } : { error: { user_message: "请先登录。" } };
      status = authenticated ? 200 : 401;
    }
    if (path === "/auth/login") {
      authenticated = true;
      body = { user };
    }
    if (path === "/model-settings") body = { providers: [], active_model: { source: "unconfigured", provider_id: null, model_id: null, model: null, model_name: null } };
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto(`${basePath}settings`);
  await expect(page).toHaveURL(new URL(`${basePath}login`, baseURL).href);
  await expect(page.getByRole("link", { name: "使用 GitHub 登录", exact: true })).toHaveAttribute("href", `${basePath}api/auth/github?next=%2Fsettings`);
  for (const [provider, label] of [["huawei", "使用华为W3账号登录"], ["google", "使用 Google 登录"], ["microsoft", "使用 Microsoft 登录"], ["apple", "使用 Apple 登录"]]) {
    await expect(page.getByRole("link", { name: label, exact: true })).toHaveAttribute("href", `${basePath}api/auth/${provider}?next=%2Fsettings`);
  }
  for (const provider of ["huawei", "google", "microsoft", "apple", "github"]) {
    const icon = page.locator(`a img[src$="/oauth/${provider}.svg"]`);
    await icon.scrollIntoViewIfNeeded();
    await expect(icon).toHaveAttribute("src", `${basePath}oauth/${provider}.svg`);
    await expect.poll(() => icon.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  }
  const logo = page.locator('img[src$="unibot-icon-v2.png"]').first();
  await expect(logo).toHaveAttribute("src", `${basePath}unibot-icon-v2.png`);
  await expect.poll(() => logo.evaluate((image) => (image as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  await page.getByRole("textbox", { name: "邮箱", exact: true }).fill(user.email);
  await page.getByLabel("密码", { exact: true }).fill("password-123");
  await page.locator("form").getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(new URL(`${basePath}settings`, baseURL).href);
  await page.reload();
  await expect(page).toHaveURL(new URL(`${basePath}settings`, baseURL).href);
  await expect(page.locator("aside").getByText(user.name, { exact: true })).toBeVisible();
  await page.locator(`aside a[href="${basePath}files"]`).first().click();
  await expect(page).toHaveURL(new URL(`${basePath}files`, baseURL).href);
  expect(apiPaths).toContain(`${basePath}api/auth/login`);
  expect(apiPaths).toContain(`${basePath}api/auth/me`);
});
