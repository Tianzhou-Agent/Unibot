import { expect, test, type Page, type Route } from "playwright/test";

const NOW = "2026-07-12T04:00:00.000Z";

type JsonObject = Record<string, unknown>;

interface MockState {
  conversations: JsonObject[];
  tools: JsonObject[];
}

function conversation(overrides: JsonObject = {}): JsonObject {
  return {
    id: "conv-e2e-1",
    user_id: "anonymous",
    tenant_id: "default",
    title: "已有会话",
    category: "general",
    status: "active",
    run_status: "idle",
    active_trace_id: null,
    run_error: null,
    run_started_at: null,
    config: {},
    enabled_ainas: [],
    messages: [],
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function installMockApi(page: Page, initial: Partial<MockState> = {}): Promise<MockState> {
  const state: MockState = {
    conversations: initial.conversations ?? [],
    tools: initial.tools ?? [],
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api/, "");
    const method = request.method();

    if (method === "GET" && path === "/conversations") {
      return json(route, state.conversations.filter((item) => item.status !== "deleted"));
    }
    if (method === "POST" && path === "/conversations") {
      const payload = request.postDataJSON() as JsonObject;
      const created = conversation({ title: payload.title, category: payload.category });
      state.conversations.push(created);
      return json(route, created, 201);
    }
    if (path.startsWith("/conversations/")) {
      const id = path.split("/")[2];
      const record = state.conversations.find((item) => item.id === id);
      if (!record) return json(route, { error: { message: "Conversation not found" } }, 404);
      if (method === "GET") {
        return record.status === "deleted"
          ? json(route, { error: { message: "Conversation not found" } }, 404)
          : json(route, record);
      }
      if (method === "PATCH") {
        Object.assign(record, request.postDataJSON(), { updated_at: NOW });
        return json(route, record);
      }
      if (method === "DELETE") {
        record.status = "deleted";
        return route.fulfill({ status: 204, body: "" });
      }
      if (method === "POST" && path.endsWith("/restore")) {
        record.status = "active";
        return json(route, record);
      }
    }
    if (method === "POST" && path === "/chat/stream") {
      const payload = request.postDataJSON() as JsonObject;
      const record = state.conversations.find((item) => item.id === payload.conversation_id);
      if (!record) return json(route, { error: { message: "Conversation not found" } }, 404);
      const reply = "这是确定性的端到端回复。";
      const messages = record.messages as JsonObject[];
      messages.push(
        { id: "msg-user", role: "user", content: payload.message, content_type: "text", widgets: [], created_at: NOW },
        { id: "msg-assistant", role: "assistant", content: reply, content_type: "text", widgets: [], created_at: NOW },
      );
      const response = {
        conversation_id: record.id,
        message_id: "msg-assistant",
        content: reply,
        status: "completed",
        trace_id: "trace-e2e-1",
        iterations: 1,
        usage: { input_tokens: 4, output_tokens: 6 },
        approval: null,
        widgets: [],
      };
      const frames = [
        `event: message.delta\ndata: ${JSON.stringify({ type: "message.delta", delta: reply })}\n\n`,
        `event: message.completed\ndata: ${JSON.stringify({ type: "message.completed", response })}\n\n`,
      ].join("");
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: frames });
    }
    if (method === "GET" && path === "/approvals") return json(route, []);
    if (method === "GET" && path === "/ainas") return json(route, []);
    if (method === "GET" && path === "/installations") return json(route, []);
    if (method === "GET" && path === "/skills") return json(route, []);
    if (method === "GET" && path === "/tools") return json(route, state.tools);
    if (method === "POST" && path === "/tools") {
      const payload = request.postDataJSON() as JsonObject;
      const created = {
        ...payload,
        version: payload.version ?? "1.0.0",
        authentication: payload.authentication ?? { type: "none", header_name: "Authorization" },
        timeout_seconds: payload.timeout_seconds ?? 15,
        retries: payload.retries ?? 1,
        permissions: payload.permissions ?? [],
        visibility: payload.visibility ?? "public",
        status: payload.status ?? "published",
        created_at: NOW,
      };
      state.tools.push(created);
      return json(route, created, 201);
    }
    if (method === "GET" && path === "/health") return json(route, { status: "ok" });
    if (method === "GET" && path === "/admin/summary") {
      return json(route, { conversations: 2, tools: 1, skills: 1, ainas: 2, installations: 1, traces: 1, memories: 3 });
    }
    if (method === "GET" && path === "/traces") {
      return json(route, [
        {
          trace_id: "trace-e2e-1",
          conversation_id: "conv-e2e-1",
          user_id: "anonymous",
          tenant_id: "default",
          status: "completed",
          events: [{ timestamp: NOW, kind: "agent.completed", status: "completed", details: {} }],
          created_at: NOW,
          completed_at: NOW,
        },
      ]);
    }
    return json(route, { error: { message: `Unhandled mock route: ${method} ${path}` } }, 501);
  });

  return state;
}

test("FE-E2E-001 新建会话并展示流式回复", async ({ page }) => {
  await installMockApi(page);
  await page.goto("/chat");

  await expect(page.getByRole("heading", { name: "开始新对话" })).toBeVisible();
  await page.getByLabel("消息").fill("请执行前端端到端测试");
  await page.getByRole("button", { name: "发送消息" }).click();

  await expect(page).toHaveURL(/\/chat\/conv-e2e-1$/);
  await expect(page.getByText("请执行前端端到端测试", { exact: true })).toBeVisible();
  await expect(page.getByText("这是确定性的端到端回复。", { exact: true })).toBeVisible();
});

test("FE-E2E-002 重命名、分类、删除并恢复会话", async ({ page }) => {
  await installMockApi(page, { conversations: [conversation()] });
  await page.goto("/chat/conv-e2e-1");

  await expect(page.getByRole("heading", { name: "已有会话" })).toBeVisible();
  await page.getByRole("button", { name: "重命名对话", exact: true }).click();
  await page.getByLabel("对话标题").fill("重命名后的会话");
  await page.getByRole("button", { name: "保存" }).click();
  await expect(page.getByRole("heading", { name: "重命名后的会话" })).toBeVisible();

  await page.getByLabel("会话分类").selectOption("work");
  await expect(page.getByLabel("会话分类")).toHaveValue("work");
  await page.getByRole("button", { name: "删除对话", exact: true }).click();
  await page.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(page.getByRole("heading", { name: "“重命名后的会话”已删除" })).toBeVisible();

  await page.getByRole("button", { name: "恢复对话" }).click();
  await expect(page.getByRole("heading", { name: "重命名后的会话" })).toBeVisible();
});

test("FE-E2E-003 在能力中心注册 Tool", async ({ page }) => {
  await installMockApi(page);
  await page.goto("/apps");

  await expect(page.getByRole("heading", { name: "能力中心" })).toBeVisible();
  await page.getByRole("button", { name: /Tools/ }).click();
  await page.getByRole("button", { name: "注册Tool" }).click();
  await expect(page.getByLabel("Tool JSON")).toHaveValue(/browser\.demo\.add/);
  await page.getByRole("button", { name: "提交注册" }).click();

  await expect(page.getByText("Tool注册成功。", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "浏览器加法 Tool" })).toBeVisible();
});

test("FE-E2E-004 查看运行摘要并开启 Trace Debug", async ({ page }) => {
  await installMockApi(page);
  await page.goto("/settings");

  await expect(page.getByText("后端在线", { exact: true })).toBeVisible();
  await expect(page.getByLabel("运行统计").getByText("3", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Debug 模式已关闭" })).toBeVisible();
  await page.getByRole("button", { name: "Debug 已关闭" }).click();

  await expect(page.getByRole("heading", { name: "调用 Trace" })).toBeVisible();
  await expect(page.getByText("trace-e2e-1", { exact: true })).toBeVisible();
});
