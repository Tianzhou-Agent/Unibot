import { expect, test, type Page, type Route } from "playwright/test";
import type { AinaRecord, ScheduledAinaExecution, ScheduledAinaTask } from "../src/types";

const NOW = "2026-10-14T01:00:00Z";
const NEXT_RUN = "2026-10-15T01:00:00Z";

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

function app(id: string, name: string, runtime: AinaRecord["manifest"]["runtime"], status: AinaRecord["status"] = "registered"): AinaRecord {
  return {
    manifest: {
      protocol_version: "1.0",
      aina: { id, name, version: "1.0.0", description: name, publisher: { id: "test", name: "Test" } },
      runtime,
      capabilities: { skills: [], tools: [], ui: [], events: [] },
      permissions: [],
      authentication: { type: "none", header_name: "Authorization" },
      health_check: null,
    },
    status,
    registered_at: NOW,
    last_health: {},
  };
}

async function installScheduleApi(page: Page, options: { apps?: AinaRecord[]; approval?: boolean } = {}) {
  const state = {
    tasks: [] as ScheduledAinaTask[],
    executions: [] as ScheduledAinaExecution[],
    creates: [] as Record<string, unknown>[],
    patches: [] as Record<string, unknown>[],
    debugInputs: [] as Record<string, unknown>[],
  };
  const apps = options.apps ?? [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api/, "");
    const method = request.method();
    if (method === "GET" && path === "/auth/config") {
      return json(route, { auth_required: false, registration_enabled: true, github_enabled: false });
    }
    if (method === "GET" && path === "/model-settings") {
      return json(route, { providers: [], active_model: { source: "unconfigured" } });
    }
    if (method === "GET" && path === "/ainas") return json(route, apps);
    if (method === "GET" && path === "/installations") {
      return json(route, apps.filter((item) => item.manifest.runtime.type !== "builtin").map((item) => ({
        aina_id: item.manifest.aina.id, user_id: "anonymous", tenant_id: "default", status: "active",
        installed_version: "1.0.0", granted_permissions: [], configuration: {}, installed_at: NOW,
      })));
    }
    if (method === "GET" && path === "/aina-schedules") return json(route, state.tasks);
    if (method === "POST" && path === "/aina-schedules") {
      const payload = request.postDataJSON();
      state.creates.push(payload);
      const task: ScheduledAinaTask = {
        id: `schedule-${state.creates.length}`, aina_id: "", user_id: "anonymous", tenant_id: "default",
        name: "", schedule_type: "interval", interval_seconds: 3600, timezone: "Asia/Shanghai",
        input: { message: payload.prompt }, enabled: true, next_run_at: NEXT_RUN,
        last_status: "never", created_at: NOW, updated_at: NOW, ...payload,
      };
      state.tasks.push(task);
      return json(route, task, 201);
    }
    const task = state.tasks.find((item) => path.startsWith(`/aina-schedules/${item.id}`));
    if (task && method === "GET" && path.endsWith("/executions")) return json(route, state.executions);
    if (task && method === "PATCH") {
      const payload = request.postDataJSON();
      state.patches.push(payload);
      Object.assign(task, payload, { input: { message: payload.prompt ?? task.prompt } });
      return json(route, task);
    }
    if (task && method === "POST" && path.endsWith("/run")) {
      const payload = request.postDataJSON();
      state.debugInputs.push(payload);
      const approval = options.approval
        ? {
            id: "approval-debug",
            conversation_id: "conv-scheduled-run",
            user_id: task.user_id,
            tenant_id: task.tenant_id,
            trace_id: "trace-scheduled-run",
            tool_calls: [
              { id: "call-debug", type: "function", function: { name: "run_python", arguments: "print(1)" } },
            ],
            capability_names: ["Run Python"],
            status: "pending",
            created_at: NOW,
          }
        : null;
      const result = {
        conversation_id: "conv-scheduled-run", trace_id: "trace-scheduled-run",
        content: options.approval ? "请确认本次操作。" : "已完成今天的摘要。",
        status: options.approval ? "approval_required" : "completed",
        approval,
      };
      const execution: ScheduledAinaExecution = {
        id: "execution-debug", task_id: task.id, aina_id: task.aina_id,
        user_id: task.user_id, tenant_id: task.tenant_id, trigger: "manual",
        call_id: "debug-call", node_id: "node-test", input: { message: payload.prompt },
        status: options.approval ? "approval_required" : "succeeded", result,
        started_at: NOW, finished_at: NOW, duration_ms: 42,
      };
      state.executions.unshift(execution);
      Object.assign(task, { last_status: execution.status, last_result: result, last_run_at: NOW });
      return json(route, task);
    }
    if (method === "POST" && path === "/approvals/approval-debug/confirm") {
      const response = {
        conversation_id: "conv-scheduled-run", message_id: "msg-after-approval",
        content: "审批已通过，任务完成。", status: "completed", trace_id: "trace-scheduled-run",
        iterations: 1, usage: { input_tokens: 1, output_tokens: 1, estimated: false },
        approval: null, widgets: [],
      };
      const task = state.tasks[0];
      if (task) Object.assign(task, { last_status: "succeeded", last_result: response, last_error: null });
      if (state.executions[0]) {
        Object.assign(state.executions[0], { status: "succeeded", result: response, finished_at: NOW, duration_ms: 1200 });
      }
      return json(route, response);
    }
    if (method === "GET") return json(route, []);
    return json(route, { error: { user_message: `Unhandled: ${method} ${path}` } }, 501);
  });
  return state;
}

async function fillTask(page: Page) {
  await page.getByLabel("任务名称", { exact: true }).fill("每日摘要");
  await page.getByLabel("任务内容", { exact: false }).fill("整理今天的信息并给出摘要");
}

test("无需安装或选择应用即可创建 Agent 任务，调试和历史保留输入输出及对话", async ({ page }) => {
  const state = await installScheduleApi(page);
  await page.goto("/schedules");
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  await expect(page.getByRole("combobox", { name: /运行方式/ })).toHaveValue("agent");
  await expect(page.getByRole("combobox", { name: /目标应用/ })).toHaveCount(0);
  await fillTask(page);
  await page.getByRole("button", { name: "保存任务", exact: true }).click();
  await expect(page.getByRole("heading", { name: "每日摘要", exact: true })).toBeVisible();
  expect(state.creates[0]).toMatchObject({ aina_id: "", prompt: "整理今天的信息并给出摘要" });
  await expect(page.getByText(/Agent 自主运行 ·/)).toBeVisible();

  await page.getByRole("button", { name: "立即调试", exact: true }).click();
  await page.getByRole("textbox", { name: "Agent 自主运行 调试任务内容", exact: true }).fill("只整理今天的重要事项");
  await page.getByRole("button", { name: "运行此任务", exact: true }).click();
  await expect(page.getByText("任务调试执行成功。", { exact: true })).toBeVisible();
  await expect(page.getByText("已完成今天的摘要。", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "查看运行对话", exact: true })).toHaveAttribute("href", "/chat/conv-scheduled-run");
  expect(state.debugInputs).toEqual([{ prompt: "只整理今天的重要事项" }]);
  expect(state.tasks[0].next_run_at).toBe(NEXT_RUN);
  expect(state.tasks[0].prompt).toBe("整理今天的信息并给出摘要");

  await page.getByRole("button", { name: "执行历史", exact: true }).click();
  await page.locator("summary").filter({ hasText: "手动调试" }).click();
  await expect(page.getByText("只整理今天的重要事项", { exact: true })).toBeVisible();
  await expect(page.getByText("已完成今天的摘要。", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "每日摘要", exact: true })).toBeVisible();
});

test("指定内置、远程或托管应用并支持编辑切换为 Agent 模式", async ({ page }) => {
  const state = await installScheduleApi(page, { apps: [
    app("unibot-code-runner", "代码运行器", { type: "builtin" }),
    app("unibot-scheduler", "定时任务 AINA", { type: "builtin" }),
    app("remote-report", "远程报告", { type: "remote", endpoint: "http://report.test", streaming: false, async_tasks: false }),
    app("managed-report", "托管报告", { type: "managed", language: "python", entrypoint: "main.py" }),
    app("disabled-app", "停用应用", { type: "builtin" }, "disabled"),
  ] });
  await page.goto("/schedules");
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  await page.getByRole("combobox", { name: /运行方式/ }).selectOption("aina");
  const target = page.getByRole("combobox", { name: /目标应用/ });
  await expect(target.locator("option")).toHaveText(["请选择要定时运行的应用", "代码运行器", "远程报告", "托管报告"]);
  await target.selectOption("unibot-code-runner");
  await fillTask(page);
  await page.getByRole("button", { name: "保存任务", exact: true }).click();
  await expect(page.getByRole("heading", { name: "每日摘要", exact: true })).toBeVisible();
  expect(state.creates[0].aina_id).toBe("unibot-code-runner");
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(target).toBeEnabled();
  await target.selectOption("managed-report");
  await page.getByRole("button", { name: "保存任务", exact: true }).click();
  await expect(page.getByText(/托管报告 ·/)).toBeVisible();
  expect(state.patches.at(-1)?.aina_id).toBe("managed-report");
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByRole("combobox", { name: /运行方式/ }).selectOption("agent");
  await page.getByRole("button", { name: "保存任务", exact: true }).click();
  await expect(page.getByText(/Agent 自主运行 ·/)).toBeVisible();
  expect(state.patches.at(-1)?.aina_id).toBe("");
});

test("需要审批的任务可在任务卡片直接确认，并把最终结果写回任务与历史", async ({ page }) => {
  await installScheduleApi(page, { approval: true });
  await page.goto("/schedules");
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  await fillTask(page);
  await page.getByRole("button", { name: "保存任务", exact: true }).click();
  await page.getByRole("button", { name: "立即调试", exact: true }).click();
  await page.getByRole("button", { name: "运行此任务", exact: true }).click();

  await expect(page.getByText("1 个任务等待确认", { exact: true })).toBeVisible();
  await expect(page.getByText("本次运行需要确认", { exact: true })).toBeVisible();
  await expect(page.getByText("需要确认 · Run Python", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "打开运行对话并确认", exact: true })).toHaveAttribute("href", "/chat/conv-scheduled-run");
  await expect(page.getByText("任务调试失败。", { exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "允许", exact: true }).click();
  await expect(page.getByText("已确认，任务继续执行并写回结果。", { exact: true })).toBeVisible();
  await expect(page.getByText("审批已通过，任务完成。", { exact: true })).toBeVisible();
  await expect(page.getByText("成功", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("1 个任务等待确认", { exact: true })).toHaveCount(0);
  await expect(page.getByText("本次运行需要确认", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "查看运行对话", exact: true })).toHaveAttribute("href", "/chat/conv-scheduled-run");
});
