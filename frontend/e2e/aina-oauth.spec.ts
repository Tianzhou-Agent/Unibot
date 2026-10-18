import { expect, test, type Page, type Route } from "playwright/test";

// OAuth tool servers (MCP authorization): installing sends the user to sign in, the callback brings them back, and a
// lost connection asks them to connect again. The API and the authorization server are mocked.
const AINA = "com.example.mail";
const NOW = "2026-10-18T04:00:00.000Z";

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

const record = {
  manifest: {
    protocol_version: "2.0",
    aina: { id: AINA, name: "Mail", version: "1.0.0", description: "Read and send the user's mail.", publisher: { id: "example", name: "Example Inc." } },
    runtime: {
      type: "package",
      archive_sha256: "0".repeat(64),
      package: {
        aina: { id: AINA, name: "Mail", version: "1.0.0", publisher: { id: "example", name: "Example Inc." } },
        tools: { mcp: { url: "https://mail.example.com/mcp", auth: { type: "oauth2" } } },
        permissions: [],
      },
      tools: [{ name: "list_mail", description: "List mail.", effect: "read", visibility: ["model", "app"], status: "active" }],
    },
    capabilities: { skills: [], tools: [], ui: [], events: [] },
    permissions: [],
  },
  status: "registered",
  registered_at: NOW,
  last_health: {},
};

function installation(status: string, connected: boolean) {
  return {
    aina_id: AINA, installed_version: "1.0.0", user_id: "anonymous", tenant_id: "default", granted_permissions: [],
    configuration: {}, status, installed_at: NOW, consent_changes: [], oauth_connected: connected,
  };
}

async function installApi(page: Page, initial: ReturnType<typeof installation>[] = []) {
  const state = { installations: initial, calls: [] as string[] };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api/, "");
    const method = request.method();
    if (path === "/auth/config") return json(route, { auth_required: false, registration_enabled: true, github_enabled: false });
    if (path === "/ainas") return json(route, [record]);
    if (path === "/installations") return json(route, state.installations);
    if (method === "POST" && path === `/ainas/${AINA}/install`) {
      state.calls.push("install");
      state.installations = [installation("authorization_required", false)];
      return json(route, state.installations[0]);
    }
    if (method === "POST" && path === `/ainas/${AINA}/oauth/authorize`) {
      const { return_to } = request.postDataJSON() as { return_to: string };
      state.calls.push(`authorize ${return_to}`);
      return json(route, { authorization_url: `http://127.0.0.1:5173/fake-authorize?return_to=${encodeURIComponent(return_to)}` });
    }
    if (method === "POST" && path === `/ainas/${AINA}/oauth/disconnect`) {
      state.calls.push("disconnect");
      state.installations = [installation("authorization_required", false)];
      return json(route, state.installations[0]);
    }
    return json(route, { error: { message: `Unhandled ${method} ${path}` } }, 404);
  });
  // The authorization server: the user approves, and Unibot's callback sends them back connected.
  await page.route("**/fake-authorize?**", (route) => {
    const returnTo = new URL(route.request().url()).searchParams.get("return_to") ?? "/plugin";
    state.installations = [installation("active", true)];
    return route.fulfill({ status: 302, headers: { Location: `${returnTo}?aina_oauth=connected&aina=${AINA}` } });
  });
  return state;
}

test("FE-E2E-OAUTH-01 installing an OAuth app sends the user to sign in and back", async ({ page }) => {
  const state = await installApi(page);
  await page.goto(`/plugin/${AINA}`);
  await page.getByRole("button", { name: "安装" }).first().click();

  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("接下来你将使用自己的账号登录 https://mail.example.com")).toBeVisible();
  await dialog.getByRole("button", { name: "安装" }).click();

  await expect(page.getByText("账号已连接。")).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/plugin/${AINA.replace(/\./g, "\\.")}$`));
  expect(state.calls).toEqual(["install", `authorize /plugin/${AINA}`]);
  await expect(page.getByRole("alert").filter({ hasText: "登录 Mail 服务后即可开始使用" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "安装" })).toHaveCount(0);
});

test("FE-E2E-OAUTH-02 a lost connection asks the user to connect again; failures say why", async ({ page }) => {
  const state = await installApi(page, [installation("authorization_required", false)]);
  await page.goto(`/plugin/${AINA}?aina_oauth=failed&aina=${AINA}&message=refresh%20revoked`);

  await expect(page.getByText("账号连接未成功：refresh revoked")).toBeVisible();
  const banner = page.getByRole("alert").filter({ hasText: "登录 Mail 服务后即可开始使用" });
  await expect(page.getByRole("button", { name: "安装" })).toHaveCount(0);
  await banner.getByRole("button", { name: "连接账号" }).click();

  await expect(page.getByText("账号已连接。")).toBeVisible();
  expect(state.calls).toEqual([`authorize /plugin/${AINA}`]);
});

test("FE-E2E-OAUTH-03 a connected account can be disconnected", async ({ page }) => {
  const state = await installApi(page, [installation("active", true)]);
  await page.goto(`/plugin/${AINA}`);
  await page.getByRole("button", { name: /Mail 的更多操作|更多/ }).click();
  await page.getByRole("menuitem", { name: "断开账号" }).click();

  await expect(page.getByRole("alert").filter({ hasText: "登录 Mail 服务后即可开始使用" })).toBeVisible();
  expect(state.calls).toEqual(["disconnect"]);
});
