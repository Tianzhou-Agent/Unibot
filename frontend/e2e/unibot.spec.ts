import { expect, test, type Page, type Route } from "playwright/test";

const NOW = "2026-07-12T04:00:00.000Z";

type JsonObject = Record<string, unknown>;

interface MockState {
  ainas: JsonObject[];
  conversations: JsonObject[];
  approvals: JsonObject[];
  tools: JsonObject[];
  modelProviders: JsonObject[];
  streamDelayMs: number;
  staleFirstConversationLoadMs: number;
  conversationLoadCount: number;
  documentTasks: JsonObject[];
  documentContent: string;
  streamWidgets: JsonObject[];
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
    ainas: initial.ainas ?? [],
    conversations: initial.conversations ?? [],
    approvals: initial.approvals ?? [],
    tools: initial.tools ?? [],
    modelProviders: initial.modelProviders ?? [],
    streamDelayMs: initial.streamDelayMs ?? 0,
    staleFirstConversationLoadMs: initial.staleFirstConversationLoadMs ?? 0,
    conversationLoadCount: 0,
    documentTasks: initial.documentTasks ?? [],
    documentContent: initial.documentContent ?? "# 使用指南\n\n## 简介\n\n旧内容。\n",
    streamWidgets: initial.streamWidgets ?? [],
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
        widgets: state.streamWidgets,
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
          kind: ainaId === "unibot-memory" ? "memory" : ainaId === "unibot-documents" ? "document" : "panel",
          title: ainaId === "unibot-memory" ? "记忆系统" : ainaId,
          description: "管理跨对话保留的长期记忆。",
          markdown: null,
          fields: [],
          actions: [],
          apps: [],
        },
      });
    }
    if (method === "GET" && path === "/ainas") return json(route, state.ainas);
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
    if (method === "GET" && path === "/documents") {
      return json(route, { items: [{ name: "guide.md", size_bytes: state.documentContent.length, modified_at: NOW }], total: 1 });
    }
    if (method === "GET" && path === "/documents/guide.md") {
      return json(route, { name: "guide.md", size_bytes: state.documentContent.length, modified_at: NOW, content: state.documentContent });
    }
    if (method === "PUT" && path === "/documents/guide.md/sections") {
      const payload = request.postDataJSON() as JsonObject;
      const sectionContent = payload.section_content as string;
      state.documentContent = payload.heading === "简介"
        ? state.documentContent.replace(/## 简介[\s\S]*$/, sectionContent)
        : sectionContent;
      return json(route, {
        name: "guide.md",
        previous_heading: payload.heading,
        heading: payload.heading,
        level: payload.heading === "使用指南" ? 1 : 2,
        occurrence: payload.occurrence ?? 1,
        revision: "revision-e2e-updated",
        size_bytes: state.documentContent.length,
        modified_at: NOW,
      });
    }
    if (method === "GET" && path === "/documents/guide.md/outline") {
      return json(route, {
        name: "guide.md",
        size_bytes: state.documentContent.length,
        revision: "revision-e2e",
        headings: [
          { index: 1, heading: "使用指南", level: 1, occurrence: 1, line_start: 1, line_end: 6 },
          { index: 2, heading: "简介", level: 2, occurrence: 1, line_start: 3, line_end: 6 },
        ],
      });
    }
    if (method === "GET" && path === "/documents/guide.md/edit-tasks") {
      return json(route, { items: state.documentTasks, total: state.documentTasks.length });
    }
    if (method === "POST" && path === "/documents/guide.md/edit-tasks") {
      const payload = request.postDataJSON() as JsonObject;
      const task = {
        id: "document-edit-e2e",
        document_name: "guide.md",
        title: payload.description,
        description: payload.description,
        status: "reviewing",
        base_revision: "revision-e2e",
        user_id: "anonymous",
        tenant_id: "default",
        version: 2,
        error: null,
        created_at: NOW,
        updated_at: NOW,
        merged_at: null,
        sections: [{
          id: "draft-section-e2e",
          heading: "简介",
          occurrence: 1,
          level: 2,
          base_content: "## 简介\n\n旧内容。\n",
          draft_content: "## 简介\n\nAI 草稿。",
          draft_revision: 1,
          ai_status: "ready",
          ai_instruction: null,
          ai_base_revision: 0,
          ai_error: null,
          updated_by: "ai",
        }],
      };
      state.documentTasks = [task];
      return json(route, task, 202);
    }
    if (method === "PATCH" && path === "/document-edit-tasks/document-edit-e2e/sections/draft-section-e2e") {
      const payload = request.postDataJSON() as JsonObject;
      const task = state.documentTasks[0];
      const section = (task.sections as JsonObject[])[0];
      section.draft_content = payload.content;
      section.draft_revision = 2;
      section.updated_by = "user";
      return json(route, task);
    }
    if (method === "POST" && path === "/document-edit-tasks/document-edit-e2e/merge") {
      const task = state.documentTasks[0];
      task.status = "merged";
      task.merged_at = NOW;
      state.documentContent = `# 使用指南\n\n${((task.sections as JsonObject[])[0]).draft_content as string}\n`;
      return json(route, task);
    }
    if (method === "GET" && /^\/documents\/[^/]+\/sections$/.test(path)) {
      const heading = url.searchParams.get("heading") ?? "未命名章节";
      const content = heading === "使用指南"
        ? state.documentContent
        : heading === "简介"
          ? state.documentContent.match(/## 简介[\s\S]*$/)?.[0] ?? "## 简介\n"
          : heading === "进阶"
            ? `## ${heading}\n\n| 用户角色 | 核心诉求 |\n| --- | --- |\n| 平台管理员 | 系统监控 |\n| 开发者 | Agent 调试 |\n| 业务用户 | 流畅交互 |`
            : `## ${heading}\n\n这是${heading}的正文内容。`;
      return json(route, {
        name: decodeURIComponent(path.split("/")[2]),
        heading,
        level: heading === "使用指南" ? 1 : 2,
        occurrence: Number(url.searchParams.get("occurrence") ?? 1),
        revision: "revision-e2e",
        content,
      });
    }
    if (method === "GET" && path === "/model-settings") {
      const activeProvider = state.modelProviders.find((provider) =>
        (provider.models as JsonObject[]).some((model) => model.is_default),
      );
      const activeModel = (activeProvider?.models as JsonObject[] | undefined)?.find((model) => model.is_default);
      return json(route, {
        providers: state.modelProviders,
        active_model: activeProvider && activeModel
          ? {
              source: "user",
              provider_id: activeProvider.id,
              provider_name: activeProvider.name,
              model_id: activeModel.id,
              model_name: activeModel.name,
              model: activeModel.model,
            }
          : { source: "environment", provider_name: "环境变量", model_name: "env-model", model: "env-model" },
      });
    }
    if (method === "POST" && /^\/model-settings\/providers\/[^/]+\/models\/[^/]+\/default$/.test(path)) {
      const [, , , providerId, , modelId] = path.split("/");
      for (const provider of state.modelProviders) {
        for (const model of provider.models as JsonObject[]) {
          model.is_default = provider.id === providerId && model.id === modelId;
        }
      }
      return json(route, state.modelProviders.find((provider) => provider.id === providerId));
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

test("FE-E2E-009 文档仅通过章节编辑或任务草稿更新", async ({ page }) => {
  await installMockApi(page);
  await page.goto("/canvas/unibot-documents");

  await expect(page.getByText("章节编辑", { exact: true })).toBeVisible();
  await expect(page.getByRole("option", { name: "使用指南", exact: true })).toHaveCount(0);
  await page.getByRole("combobox", { name: "编辑章节" }).selectOption("2");
  await page.getByRole("textbox", { name: "章节 Markdown 编辑器" }).fill("## 简介\n\n章节编辑后的内容。\n");
  await page.getByRole("button", { name: "保存章节" }).click();
  await expect(page.getByText("章节编辑后的内容。", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "任务 0" }).click();
  await expect(page.getByText("修改任务", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "新建", exact: true }).click();
  await page.getByPlaceholder("描述希望 AI 如何修改所选章节…").fill("润色简介");
  await page.getByRole("checkbox", { name: "简介" }).check();
  await page.getByRole("button", { name: "创建并执行" }).click();

  await expect(page.getByText("AI 草稿。", { exact: false })).toBeVisible();
  await page.getByRole("textbox", { name: "章节草稿" }).fill("## 简介\n\n用户检视后的内容。");
  await page.getByRole("button", { name: "保存草稿" }).click();
  await page.getByRole("button", { name: "合并全部" }).click();
  await page.getByRole("button", { name: "编辑", exact: true }).click();

  await expect(page.getByText("用户检视后的内容。", { exact: true })).toBeVisible();
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
  await page.getByRole("button", { name: /工具/ }).click();
  await page.getByRole("button", { name: "注册工具" }).click();
  await expect(page.getByLabel("工具 JSON")).toHaveValue(/browser\.demo\.add/);
  await page.getByRole("button", { name: "提交注册" }).click();

  await expect(page.getByText("工具注册成功。", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "浏览器加法工具" })).toBeVisible();
});

test("FE-E2E-003B 查看 AINA 的 Skill 提示词和 Tool Input", async ({ page }) => {
  await installMockApi(page, {
    ainas: [
      {
        manifest: {
          protocol_version: "1.0",
          aina: {
            id: "unibot-documents",
            name: "文档编辑器",
            version: "1.0.0",
            description: "管理存储在 NAS 中的 Markdown 文档。",
            publisher: { id: "unibot", name: "Unibot" },
          },
          runtime: { type: "builtin" },
          capabilities: {
            skills: [
              {
                id: "markdown-document-management",
                name: "Markdown 文档管理",
                description: "维护用户自己的 Markdown 文档。",
                input_schema: { type: "object", properties: {}, additionalProperties: false },
                instructions: "局部修改时先读取章节，再只更新目标章节。",
              },
            ],
            tools: [
              {
                id: "document.read_section",
                name: "读取文档章节",
                description: "只读取指定章节，不读取全文。",
                input_schema: {
                  type: "object",
                  properties: {
                    name: { type: "string", description: "Markdown 文档名称。" },
                    heading: { type: "string", description: "准确的章节标题。" },
                  },
                  required: ["name", "heading"],
                  additionalProperties: false,
                },
                instructions: null,
              },
            ],
            ui: [
              {
                id: "document-editor",
                kind: "document",
                description: "平台渲染的 Markdown 编辑器。",
                instructions: null,
              },
            ],
            events: [],
          },
          main_widget: null,
          permissions: [],
          authentication: { type: "none", header_name: "Authorization" },
          health_check: null,
        },
        status: "registered",
        registered_at: NOW,
        last_health: { status: "healthy" },
      },
    ],
  });
  await page.goto("/apps");

  await page.getByRole("button", { name: "查看能力", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "文档编辑器 能力详情" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("局部修改时先读取章节，再只更新目标章节。", { exact: true })).toBeVisible();
  await expect(dialog.getByText("只读取指定章节，不读取全文。", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("cell", { name: "name", exact: true })).toBeVisible();
  await expect(dialog.getByRole("cell", { name: "heading", exact: true })).toBeVisible();
  await expect(dialog.getByRole("cell", { name: "必填", exact: true })).toHaveCount(2);
  await dialog.getByRole("button", { name: "关闭能力详情" }).click();
  await expect(dialog).toBeHidden();
});

test("FE-E2E-004 查看运行摘要并开启 Trace Debug", async ({ page }) => {
  await installMockApi(page);
  await page.goto("/debug");

  await expect(page.getByText("后端在线", { exact: true })).toBeVisible();
  await expect(page.getByLabel("运行统计").getByText("3", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "调试模式已关闭" })).toBeVisible();
  await page.getByRole("button", { name: "调试模式已关闭" }).click();

  await expect(page.getByRole("heading", { name: "调用记录", exact: true })).toBeVisible();
  await expect(page.getByText("trace-e2e-1", { exact: true })).toBeVisible();
});

test("FE-E2E-004C 工具结果只按顶层 error 字段标记失败", async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem("unibot:debug-mode", "true"));
  await installMockApi(page, {
    conversations: [
      conversation({
        messages: [
          {
            id: "msg-tool-success",
            role: "tool",
            name: "builtin_document_edit_task_create_success",
            tool_call_id: "call-success",
            content: JSON.stringify({ task: { status: "queued", sections: [{ ai_error: null }] } }),
            content_type: "text",
            widgets: [],
            created_at: NOW,
          },
          {
            id: "msg-tool-error",
            role: "tool",
            name: "builtin_document_edit_task_create_error",
            tool_call_id: "call-error",
            content: JSON.stringify({ error: { code: "CONFLICT", message: "Task creation failed" } }),
            content_type: "text",
            widgets: [],
            created_at: NOW,
          },
        ],
      }),
    ],
  });

  await page.goto("/chat/conv-e2e-1");

  await expect(page.getByText("能力调用结果 · builtin_document_edit_task_create_success", { exact: true })).toBeVisible();
  await expect(page.getByText("能力调用失败 · builtin_document_edit_task_create_error", { exact: true })).toBeVisible();
});

test("FE-E2E-004D 打开应用响应会直接进入对应 Canvas", async ({ page }) => {
  await installMockApi(page, {
    conversations: [conversation()],
    streamWidgets: [
      {
        id: "open-unibot-documents",
        kind: "navigation",
        title: "打开文档编辑器",
        description: "文档应用已准备好。",
        markdown: null,
        fields: [],
        apps: [],
        actions: [
          {
            id: "open",
            label: "进入 Canvas",
            kind: "open_aina",
            aina_id: "unibot-documents",
            style: "primary",
          },
        ],
      },
    ],
  });
  await page.goto("/chat/conv-e2e-1");

  await page.getByRole("textbox", { name: "消息", exact: true }).fill("打开文档应用");
  await page.getByRole("button", { name: "发送消息" }).click();

  await expect(page).toHaveURL(/\/canvas\/unibot-documents\?conversation=conv-e2e-1$/);
});

test("FE-E2E-004B 区分应用、设置和 Debug，并切换默认模型", async ({ page }) => {
  await installMockApi(page, {
    modelProviders: [
      {
        id: "provider-e2e-1",
        provider_type: "openai",
        name: "团队模型服务",
        base_url: "https://models.example.com/v1",
        api_key_masked: "tes******-key",
        has_api_key: true,
        timeout_seconds: 60,
        models: [
          { id: "model-fast", name: "快速模型", model: "team-fast", enabled: true, is_default: false },
          { id: "model-reasoning", name: "推理模型", model: "team-reasoning", enabled: true, is_default: true },
        ],
      },
    ],
  });
  await page.goto("/settings");

  await expect(page.getByRole("link", { name: "应用", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "设置", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Debug", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "设置", exact: true })).toBeVisible();
  await expect(page.getByLabel("Provider 团队模型服务").getByText("快速模型", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Provider 团队模型服务").getByText("推理模型", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "设为默认" }).click();
  await expect(page.getByText("默认模型已切换，新对话请求将使用该模型。", { exact: true })).toBeVisible();
  await expect(page.getByText("快速模型", { exact: true }).first()).toBeVisible();

  await page.getByRole("link", { name: "Debug", exact: true }).click();
  await expect(page).toHaveURL(/\/debug$/);
  await expect(page.getByRole("heading", { name: "Debug", exact: true })).toBeVisible();
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
  await expect(page.getByRole("heading", { name: "添加记忆", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "长期记忆", exact: true })).toBeVisible();
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

test("FE-E2E-008 在章节 Widget 中选择并查看文档内容", async ({ page }) => {
  const outlineWidget = {
    id: "document-outline-e2e",
    kind: "document_outline",
    title: "使用指南.md",
    description: "选择章节即可查看对应内容。",
    markdown: null,
    fields: [],
    actions: [],
    apps: [],
    document_name: "使用指南.md",
    sections: [
      { index: 1, heading: "使用指南", level: 1, occurrence: 1, line_start: 1, line_end: 12 },
      { index: 2, heading: "入门", level: 2, occurrence: 1, line_start: 3, line_end: 7 },
      { index: 3, heading: "进阶", level: 2, occurrence: 1, line_start: 8, line_end: 12 },
    ],
  };
  await installMockApi(page, {
    conversations: [
      conversation({
        messages: [
          {
            id: "msg-outline",
            role: "assistant",
            content: "请选择要查看的章节。",
            content_type: "text",
            widgets: [outlineWidget],
            created_at: NOW,
          },
        ],
      }),
    ],
  });
  await page.goto("/chat/conv-e2e-1");

  const widget = page.getByRole("region", { name: "文档章节 使用指南.md" });
  await expect(widget.getByRole("heading", { name: "入门", exact: true })).toBeVisible();
  await expect(widget.getByText("这是入门的正文内容。", { exact: true })).toBeVisible();

  await widget.getByRole("button", { name: "进阶", exact: true }).click();
  await expect(widget.getByRole("heading", { name: "进阶", exact: true })).toBeVisible();
  await expect(widget.getByRole("columnheader", { name: "用户角色", exact: true })).toBeVisible();
  await expect(widget.getByRole("cell", { name: "系统监控", exact: true })).toHaveCSS("border-bottom-width", "1px");
  await expect(widget.getByRole("cell", { name: "Agent 调试", exact: true })).toHaveCSS("border-bottom-width", "1px");
  await expect(widget.getByRole("cell", { name: "流畅交互", exact: true })).toHaveCSS("border-bottom-width", "0px");
});
