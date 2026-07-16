import { expect, test, type Page, type Route } from "playwright/test";

const NOW = "2026-07-12T04:00:00.000Z";

type JsonObject = Record<string, unknown>;

interface MockState {
  conversations: JsonObject[];
  approvals: JsonObject[];
  tools: JsonObject[];
  streamDelayMs: number;
  staleFirstConversationLoadMs: number;
  conversationLoadCount: number;
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
    approvals: initial.approvals ?? [],
    tools: initial.tools ?? [],
    streamDelayMs: initial.streamDelayMs ?? 0,
    staleFirstConversationLoadMs: initial.staleFirstConversationLoadMs ?? 0,
    conversationLoadCount: 0,
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
        state.conversationLoadCount += 1;
        if (state.conversationLoadCount === 1 && state.staleFirstConversationLoadMs) {
          const staleRecord = structuredClone(record);
          await new Promise((resolve) => setTimeout(resolve, state.staleFirstConversationLoadMs));
          return json(route, staleRecord);
        }
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
      if (state.streamDelayMs) {
        await new Promise((resolve) => setTimeout(resolve, state.streamDelayMs));
      }
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
    if (method === "GET" && path === "/approvals") {
      const conversationId = url.searchParams.get("conversation_id");
      const status = url.searchParams.get("status");
      return json(
        route,
        state.approvals.filter(
          (item) =>
            (!conversationId || item.conversation_id === conversationId) &&
            (!status || item.status === status),
        ),
      );
    }
    if (method === "POST" && path.startsWith("/approvals/")) {
      const [, , approvalId, action] = path.split("/");
      const approval = state.approvals.find((item) => item.id === approvalId);
      if (!approval) return json(route, { error: { message: "Approval not found" } }, 404);
      const record = state.conversations.find((item) => item.id === approval.conversation_id);
      if (action === "deny") {
        approval.status = "denied";
        if (record) {
          record.run_status = "idle";
          (record.messages as JsonObject[]).push({
            id: "msg-cancelled",
            role: "assistant",
            content: "The requested operation was cancelled.",
            content_type: "text",
            widgets: [],
            created_at: NOW,
          });
        }
        return json(route, approval);
      }
    }
    if (method === "POST" && /^\/ainas\/[^/]+\/open$/.test(path)) {
      const ainaId = path.split("/")[2];
      const payload = request.postDataJSON() as JsonObject;
      const conversationId = payload.conversation_id as string | undefined;
      return json(route, {
        aina_id: ainaId,
        name: ainaId === "unibot-memory" ? "Unibot Memory" : ainaId,
        description: "管理跨对话保留的长期记忆。",
        version: "1.0.0",
        conversation_id: conversationId ?? null,
        route: `/canvas/${ainaId}${conversationId ? `?conversation=${conversationId}` : ""}`,
        main_widget: {
          id: `${ainaId}-main`,
          kind: ainaId === "unibot-memory" ? "memory" : "panel",
          title: ainaId === "unibot-memory" ? "记忆系统" : ainaId,
          description: "管理跨对话保留的长期记忆。",
          markdown: null,
          fields: [],
          actions: [],
          apps: [],
        },
      });
    }
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
    if (method === "GET" && path === "/memories") return json(route, { items: [], total: 0 });
    if (method === "GET" && path === "/memories/stats") {
      return json(route, { total: 0, fact: 0, preference: 0, goal: 0, instruction: 0 });
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
  await page.getByRole("textbox", { name: "消息", exact: true }).fill("请执行前端端到端测试");
  await page.getByRole("button", { name: "发送消息" }).click();

  await expect(page).toHaveURL(/\/chat\/conv-e2e-1$/);
  await expect(page.locator("main p").filter({ hasText: "请执行前端端到端测试" })).toBeVisible();
  await expect(page.locator("main").getByText("这是确定性的端到端回复。", { exact: true })).toBeVisible();
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
  await expect(page.getByRole("heading", { name: "重命名后的会话", exact: true })).toBeVisible();
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

test("FE-E2E-005 new conversation reloads messages after a delayed stream", async ({ page }) => {
  await installMockApi(page, { streamDelayMs: 150, staleFirstConversationLoadMs: 350 });
  await page.goto("/chat");

  await page.getByRole("textbox", { name: "\u6d88\u606f", exact: true }).fill("\u5217\u51fa\u5e94\u7528");
  await page.getByRole("button", { name: "\u53d1\u9001\u6d88\u606f" }).click();

  await expect(page).toHaveURL(/\/chat\/conv-e2e-1$/);
  await page.waitForTimeout(450);
  await expect(page.locator("main").getByText("\u8fd9\u662f\u786e\u5b9a\u6027\u7684\u7aef\u5230\u7aef\u56de\u590d\u3002", { exact: true })).toBeVisible();
});

test("FE-E2E-006 应用列表 Widget 打开对应 Canvas", async ({ page }) => {
  const appListWidget = {
    id: "unibot-app-list",
    kind: "app_list",
    title: "AINA 应用",
    description: "当前共有 1 个可用应用。",
    markdown: null,
    fields: [],
    actions: [],
    apps: [
      {
        aina_id: "unibot-memory",
        name: "Unibot Memory",
        description: "管理长期记忆。",
        version: "1.0.0",
        publisher: "Unibot",
        installed: true,
        has_main_widget: true,
      },
    ],
  };
  await installMockApi(page, {
    conversations: [
      conversation({
        messages: [
          {
            id: "msg-app-list",
            role: "assistant",
            content: "请选择应用。",
            content_type: "text",
            widgets: [appListWidget],
            created_at: NOW,
          },
        ],
      }),
    ],
  });
  await page.goto("/chat/conv-e2e-1");

  await page.getByRole("button", { name: "打开 Unibot Memory" }).click();

  await expect(page).toHaveURL(/\/canvas\/unibot-memory\?conversation=conv-e2e-1$/);
  await expect(page.getByRole("heading", { name: "Unibot Memory", exact: true }).first()).toBeVisible();
  await expect(page.getByRole("region", { name: "记忆系统" })).toBeVisible();
});

test("FE-E2E-007 拒绝高风险审批并恢复空闲状态", async ({ page }) => {
  const pendingApproval = {
    id: "approval-e2e-1",
    conversation_id: "conv-e2e-1",
    user_id: "anonymous",
    tenant_id: "default",
    trace_id: "trace-e2e-approval",
    tool_calls: [
      {
        id: "call-e2e-1",
        type: "function",
        function: { name: "tool_demo_send", arguments: '{"recipient":"qa@example.com"}' },
      },
    ],
    capability_names: ["Send message"],
    status: "pending",
    created_at: NOW,
    resolved_at: null,
  };
  const state = await installMockApi(page, {
    conversations: [conversation({ run_status: "approval_required" })],
    approvals: [pendingApproval],
  });
  await page.goto("/chat/conv-e2e-1");

  await expect(page.getByRole("region", { name: "授权确认" })).toBeVisible();
  await page.getByRole("button", { name: "拒绝" }).click();

  await expect(page.getByRole("region", { name: "授权确认" })).toBeHidden();
  await expect(page.locator("main").getByText("The requested operation was cancelled.", { exact: true })).toBeVisible();
  expect(state.approvals[0].status).toBe("denied");
});
