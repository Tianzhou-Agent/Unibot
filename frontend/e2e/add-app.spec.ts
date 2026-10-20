import { expect, test, type Page, type Route } from "playwright/test";

// The Add app page builds an AINA 2.0 package from its parts: an MCP server's chosen tools, skills and a canvas.
// The API is mocked; the backend's composition is covered by backend tests.
function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

const TOOLS = [
  { name: "describe_scene", description: "Describe the board.", effect: "read", min_effect: "read" },
  { name: "create_element", description: "Create an element.", effect: "write", min_effect: "read" },
  { name: "clear_canvas", description: "Clear the board.", effect: "destructive", min_effect: "destructive" },
];

async function installApi(page: Page) {
  const calls: { path: string; body?: unknown; form?: string }[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api/, "");
    const method = request.method();
    if (path === "/auth/config") return json(route, { auth_required: false, registration_enabled: true, github_enabled: false });
    if (method === "POST" && path === "/ainas/mcp/inspect") {
      calls.push({ path, body: request.postDataJSON() });
      return json(route, { name: "board", version: "1.0.0", tools: TOOLS });
    }
    if (method === "POST" && path === "/ainas/canvas/inspect") return json(route, { allowed: true, embeddable: true });
    if (method === "POST" && path === "/ainas/packages/compose") {
      const mode = url.searchParams.get("mode");
      calls.push({ path: `${path}?mode=${mode}`, form: request.postDataBuffer()?.toString("utf8") });
      if (mode === "register") return json(route, { manifest: { aina: { id: "local.board", name: "Board", version: "0.1.0" } } }, 201);
      return json(route, { report: { manifest: {}, files: [], skills: [], errors: [], warnings: [] }, canvas_preview: null });
    }
    return json(route, { error: { message: `Unhandled ${method} ${path}` } }, 404);
  });
  await page.route("http://canvas.example.test/**", (route) => route.fulfill({ status: 200, contentType: "text/html", body: "<h1>Board</h1>" }));
  return calls;
}

function manifestOf(form: string | undefined): Record<string, unknown> {
  const match = /name="manifest"\r\n\r\n([\s\S]*?)\r\n--/.exec(form ?? "");
  return JSON.parse(match?.[1] ?? "{}");
}

test("FE-E2E-ADD-01 an app is built from a server's chosen tools, a skill and a canvas address", async ({ page }) => {
  const calls = await installApi(page);
  await page.goto("/plugin/new");

  await page.getByPlaceholder("Whiteboard").fill("Board");
  await expect(page.getByPlaceholder("com.example.notes")).toHaveValue("local.board");
  await page.getByPlaceholder(/Draw and edit diagrams/).fill("Draw diagrams. Use when the user wants a flowchart.");

  await page.getByRole("button", { name: "添加 MCP 服务器" }).click();
  await page.getByRole("textbox", { name: "MCP 服务器地址" }).fill("http://127.0.0.1:3001/mcp");
  await page.getByRole("button", { name: "加载工具" }).click();
  await expect(page.getByText("已包含 3 / 3")).toBeVisible();
  await page.getByRole("checkbox", { name: "包含 clear_canvas" }).uncheck();
  await page.getByRole("combobox", { name: "describe_scene 的影响" }).selectOption("write");
  // A tool the server marks destructive cannot be made looser.
  await page.getByRole("checkbox", { name: "包含 clear_canvas" }).check();
  await expect(page.getByRole("combobox", { name: "clear_canvas 的影响" }).locator("option")).toHaveCount(1);
  await page.getByRole("checkbox", { name: "包含 clear_canvas" }).uncheck();

  await page.getByTestId("skill-files").setInputFiles({
    name: "SKILL.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("---\nname: draw-diagram\ndescription: Use when the user wants a diagram.\n---\n# Draw\n"),
  });
  await expect(page.getByText("Use when the user wants a diagram.")).toBeVisible();

  await page.getByRole("radio", { name: "网页地址" }).click();
  await page.getByRole("textbox", { name: "画布网页地址" }).fill("http://canvas.example.test/board/");
  const preview = page.locator('iframe[title^="预览"]');
  await expect(preview).toHaveAttribute("src", "http://canvas.example.test/board/");
  await expect(page.getByText("该页面允许被嵌入")).toBeVisible();
  // The page loads no bridge script: shown as display only, with a link to what that means.
  await expect(page.getByText("仅显示：页面可以正常显示，但没有使用 Superbot 的画布功能。")).toBeVisible({ timeout: 8000 });
  await expect(page.getByRole("link", { name: "这是什么意思" })).toHaveAttribute("href", "/plugin/help/canvas-bridge");

  await page.getByRole("button", { name: "检查应用" }).click();
  await expect(page.getByText("应用已通过 Superbot 的检查。")).toBeVisible();
  await page.getByRole("button", { name: "注册应用" }).click();
  await expect(page).toHaveURL(/\/plugin\/local\.board$/);

  const manifest = manifestOf(calls.find((call) => call.path === "/ainas/packages/compose?mode=register")?.form);
  expect(manifest).toMatchObject({
    protocol_version: "2.0",
    aina: { id: "local.board", name: "Board", description: "Draw diagrams. Use when the user wants a flowchart." },
    tools: {
      mcp: { url: "http://127.0.0.1:3001/mcp", auth: { type: "none" } },
      expose: { describe_scene: { effect: "write" }, create_element: { effect: "write" } },
    },
    skills: [{ id: "draw-diagram" }],
    canvas: { title: "Board", url: "http://canvas.example.test/board/" },
  });
  expect(Object.keys((manifest.tools as { expose: object }).expose)).not.toContain("clear_canvas");
});

test("FE-E2E-ADD-02 the name, description and a part are required before anything is sent", async ({ page }) => {
  const calls = await installApi(page);
  await page.goto("/plugin/new");
  await page.getByRole("button", { name: "检查应用" }).click();

  await expect(page.getByText("请输入名称")).toBeVisible();
  await expect(page.getByText("请说明应用做什么、何时使用")).toBeVisible();
  await expect(page.getByText("至少添加一个部分。")).toHaveClass(/text-danger/);
  expect(calls).toHaveLength(0);
});

test("FE-E2E-ADD-03 several servers name each tool's server", async ({ page }) => {
  const calls = await installApi(page);
  await page.goto("/plugin/new");
  await page.getByPlaceholder("Whiteboard").fill("Board");
  await page.getByPlaceholder(/Draw and edit diagrams/).fill("Draw diagrams.");
  for (const [index, url] of ["http://127.0.0.1:3001/mcp", "http://127.0.0.1:3002/mcp"].entries()) {
    await page.getByRole("button", { name: "添加 MCP 服务器" }).click();
    await page.getByRole("textbox", { name: "MCP 服务器地址" }).nth(index).fill(url);
    await page.getByRole("button", { name: "加载工具" }).nth(index).click();
  }
  // Two servers listing the same tool: only one of them may provide it.
  await expect(page.getByText("每个工具只能来自一个服务器。请只在一个服务器上保留 describe_scene, create_element, clear_canvas。")).toBeVisible();
  await page.getByRole("checkbox", { name: "包含 clear_canvas" }).first().uncheck();
  for (const name of ["describe_scene", "create_element", "clear_canvas"]) {
    await page.getByRole("checkbox", { name: `包含 ${name}` }).nth(1).uncheck();
  }
  await page.getByRole("checkbox", { name: "包含 clear_canvas" }).nth(1).check();
  await page.getByRole("button", { name: "下载应用包" }).click();

  await expect.poll(() => calls.some((call) => call.path === "/ainas/packages/compose?mode=download")).toBe(true);
  const manifest = manifestOf(calls.find((call) => call.path === "/ainas/packages/compose?mode=download")?.form);
  expect(manifest.tools).toMatchObject({
    mcp: [{ id: "main", url: "http://127.0.0.1:3001/mcp" }, { id: "server-2", url: "http://127.0.0.1:3002/mcp" }],
    expose: { describe_scene: { server: "main" }, create_element: { server: "main" }, clear_canvas: { server: "server-2" } },
  });
});

test("FE-E2E-ADD-04 the canvas bridge help explains when a canvas needs the script and offers it", async ({ page }) => {
  await installApi(page);
  await page.goto("/plugin/help/canvas-bridge");

  await expect(page.getByRole("heading", { name: "让画布连接 Superbot", level: 2 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "需要它吗？" })).toBeVisible();
  await expect(page.getByRole("link", { name: "aina-ui.js" })).toHaveAttribute("href", "/api/aina-ui/sdk/aina-ui.js");
  await expect(page.getByText('"name": "highlight_shapes"', { exact: false })).toBeVisible();
  await page.getByRole("navigation", { name: "导航路径" }).getByRole("link", { name: "添加应用" }).click();
  await expect(page).toHaveURL(/\/plugin\/new$/);
});
