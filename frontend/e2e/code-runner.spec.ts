import { expect, test, type Page, type Route } from "playwright/test";

const NOW = "2026-07-25T04:00:00.000Z";

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function installSandboxApi(page: Page) {
  const state: {
    sandbox: Record<string, unknown> | null;
    executions: Record<string, unknown>[];
  } = {
    sandbox: {
      id: "sandbox-e2e",
      user_id: "anonymous",
      tenant_id: "default",
      image: "unibot/python-sandbox:3.12",
      driver: "kubernetes",
      status: "ready",
      runtime_name: "unibot-e2e",
      workspace: "/workspace",
      endpoint: "unibot-e2e.unibot-sandboxes.svc",
      last_error: null,
      created_at: NOW,
      updated_at: NOW,
      last_activity_at: NOW,
    },
    executions: [],
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api/, "");
    const method = request.method();

    if (method === "POST" && path === "/ainas/unibot-code-runner/open") {
      return json(route, {
        aina_id: "unibot-code-runner",
        name: "代码运行器",
        description: "在隔离沙箱中执行脚本。",
        version: "1.0.0",
        conversation_id: null,
        route: "/canvas/unibot-code-runner",
        main_widget: {
          id: "unibot-code-runner-main",
          kind: "panel",
          title: "代码运行器",
          description: "在隔离沙箱中执行脚本。",
          markdown: null,
          fields: [],
          actions: [],
          apps: [],
        },
      });
    }
    if (method === "POST" && path === "/sandboxes/ensure") {
      state.sandbox ??= {
        id: "sandbox-e2e",
        user_id: "anonymous",
        tenant_id: "default",
        image: "unibot/python-sandbox:3.12",
        driver: "kubernetes",
        status: "ready",
        runtime_name: "unibot-e2e",
        workspace: "/workspace",
        endpoint: "unibot-e2e.unibot-sandboxes.svc",
        last_error: null,
        created_at: NOW,
        updated_at: NOW,
        last_activity_at: NOW,
      };
      state.sandbox.status = "ready";
      return json(route, state.sandbox);
    }
    if (method === "GET" && path === "/sandboxes/executions") {
      return json(route, state.executions);
    }
    if (method === "POST" && path === "/sandboxes/execute") {
      const payload = request.postDataJSON() as Record<string, unknown>;
      const execution = {
        id: `execution-e2e-${state.executions.length + 1}`,
        sandbox_id: "sandbox-e2e",
        user_id: "anonymous",
        tenant_id: "default",
        language: payload.language,
        script: payload.script,
        working_directory: payload.working_directory ?? ".",
        status: "succeeded",
        stdout: "这是保存在用户工作区的内容\n",
        stderr: "",
        exit_code: 0,
        duration_ms: 42,
        truncated: false,
        started_at: NOW,
        finished_at: NOW,
      };
      state.executions.unshift(execution);
      return json(route, execution);
    }
    if (method === "POST" && path === "/sandboxes/stop") {
      if (!state.sandbox) return json(route, { error: { message: "Sandbox not found" } }, 404);
      state.sandbox.status = "stopped";
      return json(route, state.sandbox);
    }
    if (method === "DELETE" && path === "/sandboxes/current") {
      state.sandbox = null;
      state.executions = [];
      return route.fulfill({ status: 204, body: "" });
    }
    if (method === "GET" && path.startsWith("/approvals")) return json(route, []);
    return json(route, { error: { message: `Unhandled ${method} ${path}` } }, 404);
  });
  return state;
}

test("FE-E2E-009 在代码运行器中执行脚本并查看输入输出历史", async ({ page }) => {
  const state = await installSandboxApi(page);
  await page.goto("/canvas/unibot-code-runner");

  await expect(page.getByRole("heading", { name: "代码运行器", exact: true })).toBeVisible();
  await expect(page.getByText("Kubernetes · gVisor", { exact: true })).toBeVisible();

  const editor = page.getByRole("textbox", { name: "脚本编辑器" });
  await editor.press("Control+A");
  await editor.type("print('这是保存在用户工作区的内容')");
  await page.getByRole("button", { name: "运行脚本" }).click();

  await expect(page.getByText("执行成功，退出码 0", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "运行输出", exact: true })).toBeVisible();
  await expect(page.getByText("python 输入", { exact: true })).toHaveCount(0);
  await expect(page.getByText("这是保存在用户工作区的内容", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "执行历史", exact: true })).toBeVisible();
  expect(state.executions).toHaveLength(1);
  expect(state.executions[0].script).toBe("print('这是保存在用户工作区的内容')");

  await page.getByRole("button", { name: "停止容器" }).click();
  await expect(page.getByText("运行容器已停止，工作区文件仍会保留。", { exact: true })).toBeVisible();
});
