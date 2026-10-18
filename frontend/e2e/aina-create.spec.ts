import { expect, test, type Page, type Route } from "playwright/test";

// Creating an AINA 2.0 app (design §12): a starter project, the upload review that registers a package, and the
// developer tokens `aina publish` uses. The API is mocked.
const AINA = "com.acme.notes";

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

const VALID_REPORT = {
  manifest: {
    aina: { id: AINA, name: "Acme Notes", version: "0.1.0", publisher: { id: "acme", name: "Acme" } },
    tools: {
      mcp: { url: "https://notes.acme.example/mcp", auth: { type: "bearer" } },
      expose: {
        list_notes: { effect: "read", visibility: ["model", "app"] },
        delete_note: { effect: "destructive", visibility: ["model", "app"] },
      },
    },
    canvas: { title: "Acme Notes", entry: "canvas/index.html" },
    permissions: [{ scope: "storage", optional: false }, { scope: "llm", optional: true }],
  },
  files: ["aina.json"],
  skills: [{ id: "notes", name: "Keep notes", description: "Use for short notes." }],
  errors: [],
  warnings: ["Tool 'list_notes' has no outputSchema"],
};

async function installApi(page: Page, report: unknown = VALID_REPORT) {
  const calls: { path: string; body?: unknown; form?: string }[] = [];
  const tokens: Record<string, unknown>[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api/, "");
    const method = request.method();
    if (path === "/auth/config") return json(route, { auth_required: false, registration_enabled: true, github_enabled: false });
    if (method === "GET" && ["/ainas", "/tools", "/skills", "/aina-projects"].includes(path)) return json(route, []);
    if (method === "GET" && path === "/installations") return json(route, []);
    if (method === "POST" && path === "/ainas/packages/scaffold") {
      calls.push({ path, body: request.postDataJSON() });
      return route.fulfill({ status: 200, contentType: "application/zip", headers: { "Content-Disposition": `attachment; filename="${AINA}.zip"` }, body: Buffer.from("PK\x05\x06" + "\0".repeat(18), "latin1") });
    }
    if (method === "POST" && path === "/ainas/packages/validate") return json(route, report);
    if (method === "POST" && path === "/ainas/packages") {
      calls.push({ path, form: request.postDataBuffer()?.toString("latin1") });
      return json(route, { manifest: { aina: { id: AINA, name: "Acme Notes", version: "0.1.0" } } }, 201);
    }
    if (path === "/developer-tokens" && method === "GET") return json(route, tokens);
    if (path === "/developer-tokens" && method === "POST") {
      const { name } = request.postDataJSON() as { name: string };
      const record = { id: `dtk_${tokens.length + 1}`, name, prefix: "ubt_AbCdEfGh", created_at: "2026-10-18T00:00:00Z", expires_at: "2027-01-16T00:00:00Z", last_used_at: null };
      tokens.push(record);
      return json(route, { token: "ubt_AbCdEfGhSecretValue", record }, 201);
    }
    const revoke = path.match(/^\/developer-tokens\/(.+)$/);
    if (revoke && method === "DELETE") {
      tokens.splice(tokens.findIndex((item) => item.id === revoke[1]), 1);
      return route.fulfill({ status: 204 });
    }
    return json(route, { error: { message: `Unhandled ${method} ${path}` } }, 404);
  });
  return calls;
}

test("FE-E2E-CREATE-01 a new app's starter project is downloaded from Plugins", async ({ page }) => {
  const calls = await installApi(page);
  await page.goto("/plugin");
  await page.getByRole("button", { name: "添加", exact: true }).click();
  await page.getByRole("menuitem", { name: "新建应用（AINA 2.0）" }).click();

  const form = page.getByRole("form", { name: "新建 AINA 2.0 应用" });
  await form.getByLabel("应用 ID").fill(AINA);
  await form.getByLabel("名称").fill("Acme Notes");
  await form.getByLabel("描述").fill("Short notes.");
  const download = page.waitForEvent("download");
  await form.getByRole("button", { name: "下载项目" }).click();

  expect((await download).suggestedFilename()).toBe(`${AINA}.zip`);
  await expect(page.getByText("项目已下载")).toBeVisible();
  expect(calls[0].body).toEqual({ aina_id: AINA, name: "Acme Notes", description: "Short notes.", mcp_url: "http://127.0.0.1:8770/mcp" });
});

test("FE-E2E-CREATE-02 an uploaded package is reviewed, then registered with a one-time credential", async ({ page }) => {
  const calls = await installApi(page);
  await page.goto("/plugin");
  await page.getByLabel("选择 AINA 2.0 应用包（.zip）").setInputFiles({ name: "notes.aina.zip", mimeType: "application/zip", buffer: Buffer.from("zip") });

  const dialog = page.getByRole("dialog", { name: "检查应用包" });
  await expect(dialog.getByText("应用包已通过 Unibot 的检查。")).toBeVisible();
  await expect(dialog.getByText("Tool 'list_notes' has no outputSchema")).toBeVisible();
  await expect(dialog.getByText("delete_note")).toBeVisible();
  await expect(dialog.getByText("Keep notes")).toBeVisible();
  await dialog.getByLabel("服务器凭据").fill("server-secret");
  await dialog.getByRole("button", { name: "注册" }).click();

  await expect(page.getByText("Acme Notes 0.1.0 已注册")).toBeVisible();
  const registration = calls.find((call) => call.path === "/ainas/packages")!;
  expect(registration.form).toContain('name="credential"');
  expect(registration.form).toContain("server-secret");
});

test("FE-E2E-CREATE-03 a package with problems lists them and cannot be registered", async ({ page }) => {
  await installApi(page, { ...VALID_REPORT, manifest: null, errors: ["aina.json: 'aina' is a required property"], warnings: [] });
  await page.goto("/plugin");
  await page.getByLabel("选择 AINA 2.0 应用包（.zip）").setInputFiles({ name: "broken.zip", mimeType: "application/zip", buffer: Buffer.from("zip") });

  const dialog = page.getByRole("dialog", { name: "检查应用包" });
  await expect(dialog.getByText("aina.json: 'aina' is a required property")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "注册" })).toBeDisabled();
});

test("FE-E2E-CREATE-04 a developer token is shown once, listed and revoked", async ({ page }) => {
  await installApi(page);
  page.on("dialog", (confirm) => void confirm.accept());
  await page.goto("/settings?tab=developer");

  await expect(page.getByText("还没有开发者令牌。")).toBeVisible();
  await page.getByLabel("令牌名称").fill("laptop");
  await page.getByRole("button", { name: "创建令牌" }).click();
  await expect(page.getByLabel("新的开发者令牌")).toHaveText("ubt_AbCdEfGhSecretValue");
  await expect(page.getByText("ubt_AbCdEfGh…")).toBeVisible();
  await expect(page.getByText(/aina publish notes\/package --host http:\/\/127\.0\.0\.1:5173\/api --token/)).toBeVisible();

  await page.getByRole("button", { name: "撤销 laptop" }).click();
  await expect(page.getByText("还没有开发者令牌。")).toBeVisible();
});
