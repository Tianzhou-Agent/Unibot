import { expect, test, type Page, type Route } from "playwright/test";

const NOW = "2026-07-12T04:00:00.000Z";

type JsonObject = Record<string, unknown>;

interface MockState {
  ainas: JsonObject[];
  ainaProjects: JsonObject[];
  conversations: JsonObject[];
  approvals: JsonObject[];
  tools: JsonObject[];
  modelProviders: JsonObject[];
  legacySpanIo: boolean;
  streamDelayMs: number;
  staleFirstConversationLoadMs: number;
  conversationLoadCount: number;
  documentTasks: JsonObject[];
  documentFolders: string[];
  documentName: string;
  documentContent: string;
  documentMergeConflict: boolean;
  lastStreamPayload: JsonObject | null;
  streamWidgets: JsonObject[];
  sandbox: JsonObject | null;
  sandboxExecutions: JsonObject[];
  lastProjectImportContentType: string | null;
  lastProjectScaffoldPayload: JsonObject | null;
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

function ainaProject(overrides: JsonObject = {}): JsonObject {
  return {
    id: "project-e2e-1",
    user_id: "anonymous",
    tenant_id: "default",
    source_filename: "managed-demo.aina.zip",
    archive_sha256: "d94f6f6f7bcb3d8b9c4f466e80339edb0a02b1e836c11f005d67462f41fcfa2f",
    size_bytes: 2048,
    uncompressed_size_bytes: 4096,
    file_count: 4,
    manifest: {
      protocol_version: "1.0",
      aina: {
        id: "com.example.managed-demo",
        name: "Managed Demo",
        version: "0.1.0",
        description: "A managed AINA project used by the browser test.",
        publisher: { id: "unibot-e2e", name: "Unibot E2E" },
      },
      runtime: {
        type: "managed",
        language: "python",
        entrypoint: "src/main.py",
        dependency_file: "requirements.txt",
      },
      capabilities: { skills: [], tools: [], ui: [], events: [] },
      permissions: [],
      authentication: { type: "none", header_name: "Authorization" },
      health_check: null,
    },
    status: "validated",
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function documentTask(overrides: JsonObject = {}, sectionOverrides: JsonObject = {}): JsonObject {
  return {
    id: "document-edit-e2e",
    document_name: "guide.md",
    title: "润色简介",
    description: "润色简介",
    status: "reviewing",
    base_revision: "revision-e2e",
    user_id: "anonymous",
    tenant_id: "default",
    attempt_count: 1,
    version: 2,
    error: null,
    created_at: NOW,
    updated_at: NOW,
    merged_at: null,
    abandoned_at: null,
    completed_at: null,
    deleted_at: null,
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
      review_status: "pending",
      resolved_at: null,
      result_revision: null,
      ...sectionOverrides,
    }],
    ...overrides,
  };
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function installMockApi(page: Page, initial: Partial<MockState> = {}): Promise<MockState> {
  const state: MockState = {
    ainas: initial.ainas ?? [],
    ainaProjects: initial.ainaProjects ?? [],
    conversations: initial.conversations ?? [],
    approvals: initial.approvals ?? [],
    tools: initial.tools ?? [],
    modelProviders: initial.modelProviders ?? [],
    legacySpanIo: initial.legacySpanIo ?? false,
    streamDelayMs: initial.streamDelayMs ?? 0,
    staleFirstConversationLoadMs: initial.staleFirstConversationLoadMs ?? 0,
    conversationLoadCount: 0,
    documentTasks: initial.documentTasks ?? [],
    documentFolders: initial.documentFolders ?? [],
    documentName: initial.documentName ?? "guide.md",
    documentContent: initial.documentContent ?? "# 使用指南\n\n## 简介\n\n旧内容。\n",
    documentMergeConflict: initial.documentMergeConflict ?? false,
    lastStreamPayload: null,
    streamWidgets: initial.streamWidgets ?? [],
    sandbox: initial.sandbox ?? null,
    sandboxExecutions: initial.sandboxExecutions ?? [],
    lastProjectImportContentType: null,
    lastProjectScaffoldPayload: null,
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api/, "");
    const method = request.method();

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
      return json(route, state.sandboxExecutions);
    }
    if (method === "POST" && path === "/sandboxes/execute") {
      const payload = request.postDataJSON() as JsonObject;
      const execution = {
        id: `execution-e2e-${state.sandboxExecutions.length + 1}`,
        sandbox_id: "sandbox-e2e",
        user_id: "anonymous",
        tenant_id: "default",
        language: payload.language,
        script: payload.script,
        working_directory: payload.working_directory ?? ".",
        status: "succeeded",
        stdout: "sandbox-e2e-ready\n",
        stderr: "",
        exit_code: 0,
        duration_ms: 42,
        truncated: false,
        started_at: NOW,
        finished_at: NOW,
      };
      state.sandboxExecutions.unshift(execution);
      return json(route, execution);
    }
    if (method === "POST" && path === "/sandboxes/stop") {
      if (!state.sandbox) return json(route, { error: { message: "Sandbox not found" } }, 404);
      state.sandbox.status = "stopped";
      return json(route, state.sandbox);
    }
    if (method === "DELETE" && path === "/sandboxes/current") {
      state.sandbox = null;
      state.sandboxExecutions = [];
      return route.fulfill({ status: 204, body: "" });
    }

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
      state.lastStreamPayload = payload;
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
    if (method === "POST" && path === "/aina-projects/scaffold") {
      state.lastProjectScaffoldPayload = request.postDataJSON() as JsonObject;
      return route.fulfill({
        status: 200,
        contentType: "application/zip",
        headers: { "Content-Disposition": "attachment; filename=\"com.example.my-aina-0.1.0.aina.zip\"" },
        body: Buffer.from("PK-e2e-scaffold"),
      });
    }
    if (method === "GET" && path === "/aina-projects") return json(route, state.ainaProjects);
    if (method === "POST" && path === "/aina-projects") {
      state.lastProjectImportContentType = request.headers()["content-type"] ?? null;
      const project = ainaProject();
      if (!state.ainaProjects.some((item) => item.id === project.id)) state.ainaProjects.unshift(project);
      return json(route, project, 201);
    }
    if (method === "GET" && /^\/aina-projects\/[^/]+\/archive$/.test(path)) {
      return route.fulfill({
        status: 200,
        contentType: "application/zip",
        headers: { "Content-Disposition": "attachment; filename=\"managed-demo.aina.zip\"" },
        body: Buffer.from("PK-e2e-project"),
      });
    }
    if (method === "DELETE" && /^\/aina-projects\/[^/]+$/.test(path)) {
      const projectId = decodeURIComponent(path.split("/")[2]);
      state.ainaProjects = state.ainaProjects.filter((item) => item.id !== projectId);
      return route.fulfill({ status: 204, body: "" });
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
    const documentPath = `/documents/${state.documentName}`;
    if (method === "GET" && path === "/documents") {
      return json(route, { items: [{ name: state.documentName, size_bytes: state.documentContent.length, modified_at: NOW }], total: 1 });
    }
    if (method === "GET" && path === "/documents/tree") {
      return json(route, {
        folders: state.documentFolders.map((folderPath) => ({ path: folderPath, name: folderPath.split("/").at(-1) })),
        documents: [{ name: state.documentName, size_bytes: state.documentContent.length, modified_at: NOW }],
      });
    }
    if (method === "POST" && path === "/documents/folders") {
      const payload = request.postDataJSON() as JsonObject;
      state.documentFolders.push(payload.path as string);
      return json(route, { path: payload.path, name: (payload.path as string).split("/").at(-1) }, 201);
    }
    if (method === "GET" && path === documentPath) {
      return json(route, { name: state.documentName, size_bytes: state.documentContent.length, modified_at: NOW, content: state.documentContent });
    }
    if (method === "PUT" && path === `${documentPath}/sections`) {
      const payload = request.postDataJSON() as JsonObject;
      const sectionContent = payload.section_content as string;
      state.documentContent = payload.heading === "简介"
        ? state.documentContent.replace(/## 简介[\s\S]*$/, sectionContent)
        : sectionContent;
      return json(route, {
        name: state.documentName,
        previous_heading: payload.heading,
        heading: payload.heading,
        level: payload.heading === "使用指南" ? 1 : 2,
        occurrence: payload.occurrence ?? 1,
        revision: "revision-e2e-updated",
        size_bytes: state.documentContent.length,
        modified_at: NOW,
      });
    }
    if (method === "PUT" && path === `${documentPath}/section-changes`) {
      const payload = request.postDataJSON() as JsonObject;
      state.documentContent = payload.content as string;
      return json(route, {
        name: state.documentName,
        revision: "revision-e2e-updated",
        updated_sections: ["使用指南", "简介"],
        size_bytes: state.documentContent.length,
        modified_at: NOW,
      });
    }
    if (method === "GET" && path === `${documentPath}/outline`) {
      return json(route, {
        name: state.documentName,
        size_bytes: state.documentContent.length,
        revision: "revision-e2e",
        headings: [
          { index: 1, heading: "使用指南", level: 1, occurrence: 1, line_start: 1, line_end: 6 },
          { index: 2, heading: "简介", level: 2, occurrence: 1, line_start: 3, line_end: 6 },
        ],
      });
    }
    if (method === "GET" && path === `${documentPath}/edit-tasks`) {
      return json(route, { items: state.documentTasks, total: state.documentTasks.length });
    }
    if (method === "POST" && path === `${documentPath}/edit-tasks`) {
      const payload = request.postDataJSON() as JsonObject;
      const task = {
        id: "document-edit-e2e",
        document_name: state.documentName,
        title: payload.description,
        description: payload.description,
        status: "reviewing",
        base_revision: "revision-e2e",
        user_id: "anonymous",
        tenant_id: "default",
        attempt_count: 1,
        version: 2,
        error: null,
        created_at: NOW,
        updated_at: NOW,
        merged_at: null,
        abandoned_at: null,
        completed_at: null,
        deleted_at: null,
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
          review_status: "pending",
          resolved_at: null,
          result_revision: null,
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
    if (method === "POST" && path === "/document-edit-tasks/document-edit-e2e/sections/draft-section-e2e/merge") {
      const task = state.documentTasks[0];
      const section = (task.sections as JsonObject[])[0];
      if (state.documentMergeConflict) {
        task.status = "conflict";
        task.error = "Document revision changed. Review the latest document before merging.";
        return json(route, { error: { user_message: task.error } }, 409);
      }
      task.status = "merged";
      task.merged_at = NOW;
      task.completed_at = NOW;
      section.review_status = "merged";
      section.resolved_at = NOW;
      section.result_revision = "revision-e2e-merged";
      state.documentContent = `# 使用指南\n\n${section.draft_content as string}\n`;
      return json(route, task);
    }
    if (method === "POST" && path === "/document-edit-tasks/document-edit-e2e/sections/draft-section-e2e/abandon") {
      const task = state.documentTasks[0];
      const section = (task.sections as JsonObject[])[0];
      task.status = "abandoned";
      task.abandoned_at = NOW;
      task.completed_at = NOW;
      section.review_status = "abandoned";
      section.resolved_at = NOW;
      return json(route, task);
    }
    if (method === "POST" && /^\/document-edit-tasks\/[^/]+\/retry$/.test(path)) {
      const taskId = path.split("/")[2];
      const task = state.documentTasks.find((item) => item.id === taskId);
      if (!task) return json(route, { error: { user_message: "Task not found" } }, 404);
      task.status = "queued";
      task.error = null;
      task.attempt_count = Number(task.attempt_count ?? 1) + 1;
      for (const section of task.sections as JsonObject[]) {
        if (section.review_status === "pending" && section.ai_status === "failed") section.ai_status = "queued";
      }
      return json(route, task, 202);
    }
    if (method === "POST" && /^\/document-edit-tasks\/[^/]+\/abandon$/.test(path)) {
      const taskId = path.split("/")[2];
      const task = state.documentTasks.find((item) => item.id === taskId);
      if (!task) return json(route, { error: { user_message: "Task not found" } }, 404);
      const sections = task.sections as JsonObject[];
      for (const section of sections) {
        if (section.review_status === "pending") {
          section.review_status = "abandoned";
          section.resolved_at = NOW;
        }
      }
      task.status = sections.some((section) => section.review_status === "merged") ? "completed" : "abandoned";
      task.abandoned_at = NOW;
      task.completed_at = NOW;
      return json(route, task);
    }
    if (method === "DELETE" && /^\/document-edit-tasks\/[^/]+$/.test(path)) {
      const taskId = path.split("/")[2];
      state.documentTasks = state.documentTasks.filter((task) => task.id !== taskId);
      return route.fulfill({ status: 204 });
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
    if (method === "POST" && path === "/model-settings/providers/discover-models") {
      return json(route, {
        models: [
          { id: "team-fast", name: "快速模型" },
          { id: "team-coder", name: "代码模型" },
        ],
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
      return json(route, { conversations: 2, tools: 1, skills: 1, ainas: 2, installations: 1, traces: 1, llm_calls: 1, memories: 3 });
    }
    if (method === "GET" && path === "/traces") {
      return json(route, [
        {
          trace_id: "trace-e2e-1",
          conversation_id: "conv-e2e-1",
          user_id: "anonymous",
          tenant_id: "default",
          status: "completed",
          events: [
            { timestamp: NOW, kind: "user.request", status: "completed", details: { content: "排查模型调用", requested_capability: null } },
            { timestamp: NOW, kind: "context.compacted", status: "completed", details: { before_tokens: 110, after_tokens: 90 } },
            { timestamp: NOW, kind: "agent.completed", status: "completed", details: {} },
            { timestamp: NOW, kind: "final.response", status: "completed", details: { content: "模型返回正常", input_tokens: 90, output_tokens: 30 } },
          ],
          root_span_id: "span-agent-e2e-1",
          spans: [
            {
              span_id: "span-agent-e2e-1",
              parent_span_id: null,
              kind: "agent",
              name: "agent.run",
              status: "completed",
              target_id: "unibot",
              target_version: null,
              logical_call_id: null,
              attempt_no: 1,
              started_at: NOW,
              first_output_at: null,
              completed_at: NOW,
              duration_ms: 180,
              input: state.legacySpanIo ? null : { message: "排查模型调用", requested_capability: null, preferred_aina_id: null },
              output: state.legacySpanIo ? null : { content: "模型返回正常", status: "completed" },
              attributes: { conversation_id: "conv-e2e-1" },
              error: null,
            },
            {
              span_id: "span-model-e2e-1",
              parent_span_id: "span-agent-e2e-1",
              kind: "model",
              name: "model.complete",
              status: "completed",
              target_id: "debug-model",
              target_version: null,
              logical_call_id: "model_iteration_1",
              attempt_no: 1,
              started_at: NOW,
              first_output_at: NOW,
              completed_at: NOW,
              duration_ms: 129,
              attributes: { input_tokens: 90, output_tokens: 30, ttft_ms: 42 },
              error: null,
            },
            {
              span_id: "span-tool-e2e-1",
              parent_span_id: "span-agent-e2e-1",
              kind: "tool",
              name: "demo.lookup",
              status: "completed",
              target_id: "demo.lookup",
              target_version: "1.0.0",
              logical_call_id: "call-e2e-1",
              attempt_no: 1,
              started_at: NOW,
              first_output_at: null,
              completed_at: NOW,
              duration_ms: 21,
              input: { query: "Unibot" },
              output: { result: "工具返回正常" },
              attributes: {},
              error: null,
            },
          ],
          created_at: NOW,
          completed_at: NOW,
        },
      ]);
    }
    if (method === "GET" && path === "/llm-calls") {
      return json(route, [
        {
          call_id: "llm-e2e-1",
          trace_id: "trace-e2e-1",
          span_id: "span-model-e2e-1",
          context_type: "conversation",
          context_id: "conv-e2e-1",
          endpoint: "https://provider.example/v1/chat/completions",
          model: "debug-model",
          status: "completed",
          request: {
            model: "debug-model",
            messages: [{ role: "user", content: "排查模型调用" }],
            stream: true,
            context_window: 128000,
          },
          response: {
            object: "chat.completion",
            choices: [{ index: 0, message: { role: "assistant", content: "模型返回正常" }, finish_reason: "stop" }],
            usage: { prompt_tokens: 90, completion_tokens: 30, total_tokens: 120, prompt_tokens_details: { cached_tokens: 20 } },
          },
          duration_ms: 128.5,
          ttft_ms: 42,
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

  await expect(page.getByText("全文编辑", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "章节", exact: true }).click();
  await expect(page.getByText("章节导航", { exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "全文 Markdown 编辑器" })).toHaveValue(/# 使用指南/);
  await page.getByRole("textbox", { name: "全文 Markdown 编辑器" }).fill("# 使用指南\n\n## 简介\n\n章节编辑后的内容。\n");
  await page.getByRole("button", { name: "保存文档" }).click();
  await expect(page.getByText("章节编辑后的内容。", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "任务 0" }).click();
  await expect(page.getByText("文档变更任务", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "新建修改任务", exact: true }).click();
  await page.getByPlaceholder("描述希望 AI 如何修改所选章节…").fill("润色简介");
  await page.getByRole("checkbox", { name: "简介" }).check();
  await page.getByRole("button", { name: "创建并执行" }).click();

  await expect(page.getByText("AI 草稿。", { exact: false })).toBeVisible();
  await expect(page.getByLabel("章节差异")).toBeVisible();
  await expect(page.getByLabel("章节差异").getByText("旧内容。", { exact: true })).toBeVisible();
  await expect(page.getByLabel("章节差异").getByText("AI 草稿。", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "放弃本章节" })).toBeVisible();
  await expect(page.getByPlaceholder("描述希望 AI 如何继续修改当前章节")).toBeVisible();
  await expect(page.getByPlaceholder("让 AI 继续修改当前章节…")).toHaveCount(0);
  await page.getByRole("button", { name: "对照编辑", exact: true }).click();
  await page.getByRole("textbox", { name: "章节草稿" }).fill("## 简介\n\n用户检视后的内容。");
  await expect(page.getByRole("button", { name: "合入本章节", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "保存草稿", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "保存草稿" }).click();
  await expect(page.getByRole("button", { name: "保存草稿", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "合入本章节", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "合入本章节" }).click();
  await expect(page.getByText("只会将当前章节的草稿写入正式文档，任务中的其他章节仍保持待检视状态。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "合入本章节", exact: true }).last().click();
  await page.getByRole("button", { name: "编辑", exact: true }).click();

  await expect(page.getByText("用户检视后的内容。", { exact: true })).toBeVisible();
});

test("FE-E2E-009B 文档 Canvas 展示嵌套文件树和全文章节导航", async ({ page }) => {
  await installMockApi(page, {
    documentFolders: ["Projects", "Projects/Specs"],
    documentName: "Projects/Specs/guide.md",
  });
  await page.goto("/canvas/unibot-documents");

  await expect(page.getByRole("button", { name: "Projects", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Specs", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /guide\.md/ })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "全文 Markdown 编辑器" })).toHaveValue(/旧内容/);
  await page.getByRole("button", { name: "章节", exact: true }).click();
  await expect(page.getByText("章节导航", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "新建文件夹", exact: true }).click();
  await page.getByRole("textbox", { name: "新文件夹名称" }).fill("Archive");
  await page.getByRole("button", { name: "确认创建" }).click();
  await expect(page.getByRole("button", { name: "Archive", exact: true })).toBeVisible();
});

test("FE-E2E-009C 全文编辑器自动保存多个章节和章节外内容", async ({ page }) => {
  const state = await installMockApi(page);
  await page.goto("/canvas/unibot-documents");

  await page.getByRole("textbox", { name: "全文 Markdown 编辑器" }).fill(
    "保存后的说明。\n\n# 被修改的根标题\n\n## 简介\n\n同时修改多个范围。\n",
  );
  await page.getByRole("button", { name: "保存文档" }).click();

  await expect(page.getByRole("textbox", { name: "全文 Markdown 编辑器" })).toHaveValue(
    "保存后的说明。\n\n# 被修改的根标题\n\n## 简介\n\n同时修改多个范围。\n",
  );
  expect(state.documentContent).toContain("保存后的说明");
  expect(state.documentContent).toContain("# 被修改的根标题");
  expect(state.documentContent).toContain("同时修改多个范围");
});

test("FE-E2E-009D 零合入任务结束后归入失败记录", async ({ page }) => {
  const state = await installMockApi(page);
  await page.goto("/canvas/unibot-documents");

  await page.getByRole("button", { name: "任务 0" }).click();
  await page.getByRole("button", { name: "新建修改任务", exact: true }).click();
  await page.getByPlaceholder("描述希望 AI 如何修改所选章节…").fill("润色简介");
  await page.getByRole("checkbox", { name: "简介" }).check();
  await page.getByRole("button", { name: "创建并执行" }).click();
  await page.getByRole("button", { name: "结束任务", exact: true }).click();

  await expect(page.getByText("所有未处理的章节草稿都将放弃，正式文档不会改变；任务随后归入失败记录。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "结束并放弃剩余改动", exact: true }).click();
  await expect(page.getByText("此任务未产生任何合入，已归入失败记录。可以查看详情或删除记录。", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "删除任务", exact: true })).toBeVisible();
  expect(state.documentContent).toContain("旧内容");
});

test("FE-E2E-009E 章节合入冲突只展示一次任务错误", async ({ page }) => {
  await installMockApi(page, { documentMergeConflict: true });
  await page.goto("/canvas/unibot-documents");

  await page.getByRole("button", { name: "任务 0" }).click();
  await page.getByRole("button", { name: "新建修改任务", exact: true }).click();
  await page.getByPlaceholder("描述希望 AI 如何修改所选章节…").fill("润色简介");
  await page.getByRole("checkbox", { name: "简介" }).check();
  await page.getByRole("button", { name: "创建并执行" }).click();
  await page.getByRole("button", { name: "合入本章节" }).click();
  await page.getByRole("button", { name: "合入本章节", exact: true }).last().click();

  await expect(page.getByText("Document revision changed. Review the latest document before merging.", { exact: true })).toHaveCount(1);
  await expect(page.getByText("合入失败", { exact: true })).toBeVisible();
});

test("FE-E2E-009F 左侧对话保留右侧任务状态并携带章节上下文", async ({ page }) => {
  const state = await installMockApi(page, { streamDelayMs: 300 });
  await page.goto("/canvas/unibot-documents");

  await page.getByRole("button", { name: "任务 0" }).click();
  await page.getByRole("button", { name: "新建修改任务", exact: true }).click();
  await page.getByPlaceholder("描述希望 AI 如何修改所选章节…").fill("润色简介");
  await page.getByRole("checkbox", { name: "简介" }).check();
  await page.getByRole("button", { name: "创建并执行" }).click();
  await expect(page.getByText("对话上下文：润色简介 / 简介", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "对照编辑", exact: true }).click();
  const draft = page.getByRole("textbox", { name: "章节草稿" });
  await draft.fill("## 简介\n\n右侧未保存的人工修改。");
  await page.getByRole("textbox", { name: "画布消息" }).fill("继续润色当前章节");
  await page.getByRole("button", { name: "发送画布消息" }).click();
  await expect(draft).toBeEnabled();
  await draft.fill("## 简介\n\n模型运行期间继续编辑。");
  await expect(page.getByText("这是确定性的端到端回复。", { exact: true })).toBeVisible();

  await expect(draft).toHaveValue("## 简介\n\n模型运行期间继续编辑。");
  await expect(page.getByText("对话上下文：润色简介 / 简介", { exact: true })).toBeVisible();
  expect(state.lastStreamPayload?.preferred_aina_id).toBe("unibot-documents");
  expect(state.lastStreamPayload?.ui_context).toContain("任务 ID：document-edit-e2e");
  expect(state.lastStreamPayload?.ui_context).toContain("章节 ID：draft-section-e2e");
});

test("FE-E2E-009G 未合入任务归入失败且合入历史按日期展示", async ({ page }) => {
  await installMockApi(page, {
    documentTasks: [
      documentTask({
        id: "task-active",
        title: "进行中任务",
        description: "等待用户检视",
        status: "reviewing",
      }, {
        id: "section-active",
      }),
      documentTask({
        id: "task-failed",
        title: "失败任务",
        description: "重新生成失败章节",
        status: "failed",
        attempt_count: 2,
        error: "Model request failed",
      }, {
        id: "section-failed",
        ai_status: "failed",
        ai_error: "Model request failed",
      }),
      documentTask({
        id: "task-abandoned",
        title: "未合入任务",
        description: "用户结束且没有产生合入",
        status: "abandoned",
        completed_at: NOW,
        abandoned_at: NOW,
      }, {
        id: "section-abandoned",
        review_status: "abandoned",
        resolved_at: NOW,
      }),
      documentTask({
        id: "task-history",
        title: "历史任务",
        description: "已经完成的变更",
        status: "merged",
        completed_at: NOW,
        merged_at: NOW,
      }, {
        id: "section-history",
        review_status: "merged",
        resolved_at: NOW,
        result_revision: "8ab9d4c",
      }),
    ],
  });
  await page.goto("/canvas/unibot-documents");

  await page.getByRole("button", { name: "任务 4", exact: true }).click();
  await expect(page.getByRole("button", { name: /^进行中\s*1$/ })).toBeVisible();
  const activeTasks = page.getByLabel("修改任务列表");
  await expect(activeTasks.getByText("task-act", { exact: true })).toBeVisible();
  await expect(activeTasks.getByText(/更新于/)).toBeVisible();
  await expect(activeTasks.getByText("<>", { exact: true })).toHaveCount(0);
  await activeTasks.getByRole("button", { name: "查看任务 进行中任务", exact: true }).click();
  await expect(page.getByRole("button", { name: "返回任务列表", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "返回任务列表", exact: true }).click();
  await page.getByRole("button", { name: /^失败\s*2$/ }).click();
  const failedTasks = page.getByLabel("修改任务列表");
  await expect(page.getByText("task-fai", { exact: true })).toBeVisible();
  await expect(page.getByText("task-aba", { exact: true })).toBeVisible();
  await expect(failedTasks.getByText("<>", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "复制任务 ID task-fai", exact: true }).click();
  await expect(page.getByRole("button", { name: "已复制任务 ID task-fai", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "查看任务 失败任务", exact: true }).click();
  await expect(page.getByRole("button", { name: "重试未完成", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "删除任务", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "删除任务", exact: true }).click();
  await page.getByRole("button", { name: "删除任务", exact: true }).last().click();
  await page.getByRole("button", { name: "查看任务 未合入任务", exact: true }).click();
  await expect(page.getByText("此任务未产生任何合入，已归入失败记录。可以查看详情或删除记录。", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "删除任务", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "返回任务列表", exact: true }).click();

  await page.getByRole("button", { name: /^历史\s*1$/ }).click();
  const history = page.getByLabel("合入历史");
  await expect(history.getByText("2026年7月12日的合入", { exact: true })).toBeVisible();
  await expect(history.getByText("task-his", { exact: true })).toBeVisible();
  await expect(history.getByText("合入于 12:00", { exact: true })).toBeVisible();
  await expect(history.getByText("已经完成的变更", { exact: true })).toHaveCount(0);
  await expect(history.getByText("全部已合入", { exact: true })).toHaveCount(0);
  await expect(history.getByText("<>", { exact: true })).toHaveCount(1);
  await expect(history.getByText("anonymous", { exact: true })).toHaveCount(0);
  await history.getByText("历史任务", { exact: true }).click();
  await expect(history).toBeVisible();
  await history.getByRole("button", { name: "复制任务 ID task-his", exact: true }).click();
  await expect(history.getByRole("button", { name: "已复制任务 ID task-his", exact: true })).toBeVisible();
  await history.getByRole("button", { name: "查看任务 历史任务", exact: true }).click();
  await expect(page.getByText(/所有章节均已合入文档，记录已锁定/)).toBeVisible();
  await expect(page.getByText(/文档 revision 8ab9d4c/)).toBeVisible();
  await expect(page.getByRole("button", { name: "删除任务", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "重试未完成", exact: true })).toHaveCount(0);
});

test("FE-E2E-002 在侧栏重命名并删除会话", async ({ page }) => {
  await installMockApi(page, { conversations: [conversation()] });
  await page.goto("/chat/conv-e2e-1");

  await expect(page.getByRole("heading", { name: "已有会话" })).toBeVisible();
  const conversationList = page.getByRole("navigation", { name: "对话列表" });
  const conversationRow = page.getByTestId("conversation-row-conv-e2e-1");
  await conversationRow.hover();
  await conversationList.getByRole("button", { name: "更多操作", exact: true }).click();
  await page.getByRole("menuitem", { name: "重命名", exact: true }).click();
  await page.getByLabel("对话标题").fill("重命名后的会话");
  await page.getByLabel("对话标题").press("Enter");
  await expect(conversationList.getByText("重命名后的会话", { exact: true })).toBeVisible();

  await conversationRow.hover();
  await conversationList.getByRole("button", { name: "更多操作", exact: true }).click();
  await page.getByRole("menuitem", { name: "删除", exact: true }).click();
  await conversationList.getByRole("button", { name: "确认删除 重命名后的会话", exact: true }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await expect(conversationList.getByText("重命名后的会话", { exact: true })).toHaveCount(0);
});

test("FE-E2E-002B 对话操作按钮悬停时显示并垂直居中", async ({ page }) => {
  await installMockApi(page, { conversations: [conversation()] });
  await page.goto("/chat/conv-e2e-1");

  const row = page.getByTestId("conversation-row-conv-e2e-1");
  const moreButton = row.getByRole("button", { name: "更多操作", exact: true });
  await expect(moreButton).toHaveCSS("opacity", "0");

  await row.hover();
  await expect(moreButton).toHaveCSS("opacity", "1");

  const rowBox = await row.boundingBox();
  const buttonBox = await moreButton.boundingBox();
  expect(rowBox).not.toBeNull();
  expect(buttonBox).not.toBeNull();
  expect(Math.abs((buttonBox!.y + buttonBox!.height / 2) - (rowBox!.y + rowBox!.height / 2))).toBeLessThan(2);

  await moreButton.click();
  await expect(page.getByRole("menu", { name: "已有会话 对话操作", exact: true })).toBeVisible();
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

test("FE-E2E-003C AINA Project 模板、导入、下载和删除保持独立闭环", async ({ page }) => {
  const state = await installMockApi(page);
  await page.goto("/apps");

  await page.getByRole("button", { name: "项目模板", exact: true }).click();
  await expect(page.getByRole("heading", { name: "生成 AINA Project 模板", exact: true })).toBeVisible();
  const scaffoldDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载 ZIP 模板", exact: true }).click();
  await expect((await scaffoldDownload).suggestedFilename()).toBe("com.example.my-aina-0.1.0.aina.zip");
  expect(state.lastProjectScaffoldPayload?.aina_id).toBe("com.example.my-aina");

  await page.getByLabel("选择 AINA Project ZIP").setInputFiles({
    name: "managed-demo.aina.zip",
    mimeType: "application/zip",
    buffer: Buffer.from("PK-e2e-upload"),
  });

  const projectSection = page.getByRole("region", { name: "AINA Projects", exact: true });
  await expect(projectSection.getByRole("heading", { name: "Managed Demo", exact: true })).toBeVisible();
  await expect(projectSection.getByText("已校验·待部署", { exact: true })).toBeVisible();
  await expect(page.getByText("尚未注册 AINA", { exact: true })).toBeVisible();
  expect(state.lastProjectImportContentType).toContain("multipart/form-data; boundary=");
  expect(state.ainas).toHaveLength(0);

  const archiveDownload = page.waitForEvent("download");
  await projectSection.getByRole("button", { name: "下载项目 Managed Demo", exact: true }).click();
  await expect((await archiveDownload).suggestedFilename()).toBe("managed-demo.aina.zip");

  page.once("dialog", (dialog) => dialog.accept());
  await projectSection.getByRole("button", { name: "删除项目 Managed Demo", exact: true }).click();
  await expect(projectSection.getByRole("heading", { name: "Managed Demo", exact: true })).toHaveCount(0);
  await expect(page.getByText("Managed Demo 项目已删除。", { exact: true })).toBeVisible();
  expect(state.ainaProjects).toHaveLength(0);
});

test("FE-E2E-004 查看运行摘要并开启 Trace OBS", async ({ page }) => {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.addInitScript(() => window.localStorage.setItem("unibot:mock-role", "admin"));
  await installMockApi(page, { conversations: [conversation()] });
  await page.goto("/obs");

  await expect(page.getByText("后端异常", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("前端 Mock 范围")).toBeVisible();
  await expect(page.getByRole("button", { name: "我的数据", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "全部用户", exact: true }).click();
  await expect(page.getByLabel("运行统计").getByText("3", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "调试模式已关闭" })).toBeVisible();
  await page.getByRole("button", { name: "开启", exact: true }).click();

  await expect(page.getByText("trace-e2e-1", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "复制 Trace ID trace-e2e-1", exact: true }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe("trace-e2e-1");
  await page.getByText("trace-e2e-1", { exact: true }).click();
  await expect(page.getByRole("button", { name: "调用链", exact: true })).toBeVisible();
  const spanTree = page.getByLabel("Span 调用树");
  await expect(spanTree.getByText("agent.run", { exact: true })).toBeVisible();
  await expect(spanTree.getByText("model.complete", { exact: true })).toBeVisible();
  await expect(spanTree.getByText("TTFT 42 ms", { exact: true })).toBeVisible();
  await spanTree.getByText("agent.run", { exact: true }).click();
  await expect(spanTree.getByLabel("agent.run 输入")).toContainText("排查模型调用");
  await expect(spanTree.getByLabel("agent.run 输出")).toContainText("模型返回正常");
  await spanTree.getByText("model.complete", { exact: true }).click();
  await expect(spanTree.getByLabel("model.complete 输入")).toContainText("排查模型调用");
  await expect(spanTree.getByLabel("model.complete 输出")).toContainText("模型返回正常");
  await spanTree.getByText("demo.lookup", { exact: true }).click();
  await expect(spanTree.getByLabel("demo.lookup 输入")).toContainText("Unibot");
  await expect(spanTree.getByLabel("demo.lookup 输出")).toContainText("工具返回正常");
  await page.getByRole("button", { name: "模型请求 1", exact: true }).click();
  await expect(page.getByRole("button", { name: "已有会话 1 Trace conv-e2e-1", exact: true })).toBeVisible();
  const modelRequestList = page.getByLabel("当前 Trace 的模型请求");
  await expect(modelRequestList.getByRole("button", {
    name: "请求 1 成功 debug-model 129 ms 120 Token",
    exact: true,
  })).toBeVisible();
  await expect(page.getByText("总耗时 129 ms · 120 Token · 233.5 Output Token/s", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "复制 Conversation ID conv-e2e-1", exact: true }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe("conv-e2e-1");
  await expect(page.getByRole("heading", { name: "llm-e2e-1", exact: true })).toBeVisible();
  const requestJson = page.getByLabel("模型请求 JSON");
  await expect(requestJson).toContainText("排查模型调用");
  await expect(requestJson.locator("pre")).toHaveClass(/whitespace-pre-wrap/);
  await expect(requestJson.locator("span.text-sky-300").filter({ hasText: "\"messages\"" })).toBeVisible();
  const requestMessage = requestJson.locator("span.text-emerald-300").filter({ hasText: "\"排查模型调用\"" });
  await expect(requestMessage).toBeVisible();
  await requestJson.getByRole("button", { name: "折叠 messages", exact: true }).click();
  await expect(requestJson.getByRole("button", { name: "展开 messages", exact: true })).toBeVisible();
  await expect(requestMessage).toHaveCount(0);
  await page.getByRole("button", { name: "响应", exact: true }).click();
  await expect(page.getByLabel("模型响应 JSON")).toContainText("模型返回正常");
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

test("FE-E2E-004B 区分应用、设置和 OBS，并切换默认模型", async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem("unibot:mock-role", "admin"));
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
  await expect(page.getByRole("link", { name: "OBS", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "设置", exact: true })).toBeVisible();
  const providerSection = page.getByLabel("Provider 团队模型服务");
  await providerSection.getByRole("heading", { name: "团队模型服务", exact: true }).click();
  await expect(providerSection.getByText("快速模型", { exact: true })).toBeVisible();
  await expect(providerSection.getByText("推理模型", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "编辑 团队模型服务", exact: true }).click();
  await page.getByRole("button", { name: "自动获取", exact: true }).click();
  await expect(page.getByText("已从 Provider 自动添加 1 个模型。", { exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "模型 3 ID", exact: true })).toHaveValue("team-coder");
  await page.getByRole("button", { name: "关闭", exact: true }).click();

  await page.getByRole("button", { name: "设为默认" }).click();
  await expect(page.getByText("默认模型已切换，新对话请求将使用该模型。", { exact: true })).toBeVisible();
  await expect(page.getByLabel("当前模型").getByText("快速模型", { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "OBS", exact: true }).click();
  await expect(page).toHaveURL(/\/obs$/);
  await expect(page.getByRole("heading", { name: "OBS", exact: true })).toBeVisible();
});

test("FE-E2E-IR-001 普通用户与管理员 Mock 入口隔离", async ({ page }) => {
  await installMockApi(page, {
    conversations: [conversation({
      messages: [
        { id: "msg-user-obs", role: "user", content: "排查模型调用", content_type: "text", widgets: [], created_at: NOW },
        { id: "msg-assistant-obs", role: "assistant", content: "模型返回正常", content_type: "text", widgets: [], trace_id: "trace-e2e-1", created_at: NOW },
      ],
    })],
    legacySpanIo: true,
  });
  await page.goto("/admin/observability");

  await expect(page.getByRole("heading", { name: "需要管理员权限", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "可观测", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "OBS", exact: true })).toBeVisible();

  await page.getByRole("link", { name: "OBS", exact: true }).click();
  await expect(page).toHaveURL(/\/obs$/);
  await expect(page.getByRole("heading", { name: "OBS", exact: true })).toHaveCount(0);
  await expect(page.getByText("后端异常", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "全部用户", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "个人总览", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "个人总览", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "当前对话", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Token 消耗日历")).toBeVisible();

  await page.goto("/chat/conv-e2e-1");
  await page.getByRole("link", { name: "查看当前对话观测数据", exact: true }).click();
  await expect(page).toHaveURL(/\/obs\?sessionId=conv-e2e-1$/);
  await expect(page.getByRole("button", { name: "当前对话", exact: true })).toBeVisible();
  await expect(page.getByLabel("选择对话")).toHaveCount(0);
  await expect(page.getByLabel("交互轮次")).toHaveCount(0);
  const obsSelector = page.getByLabel("OBS 视图选择");
  await expect(obsSelector.getByText("已有会话", { exact: true })).toBeVisible();
  await expect(obsSelector.getByText("Session ID：conv-e2e-1", { exact: true })).toBeVisible();
  await expect(page.getByText("1 轮交互 · 3 个 Span", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "交互概览", exact: true })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "对话总览", exact: true })).toHaveCount(0);
  const conversationOverview = page.getByLabel("对话数据总览");
  await expect(page.locator('[aria-label="对话数据总览"], [aria-label="当前对话分析维度"]')).toHaveCount(2);
  await expect(page.locator('[aria-label="对话数据总览"], [aria-label="当前对话分析维度"]').first()).toHaveAttribute("aria-label", "对话数据总览");
  await expect(conversationOverview.getByLabel("Token 使用")).toContainText("120");
  await expect(conversationOverview.getByLabel("Token 使用")).toContainText("输入 Token");
  await expect(conversationOverview.getByLabel("Token 使用")).toContainText("输出 Token");
  await expect(conversationOverview.getByLabel("Token 使用")).toContainText("缓存读取");
  await expect(conversationOverview.getByLabel("Token 生成速率")).toContainText("933.9 Token/s");
  await expect(conversationOverview.getByLabel("Token 生成速率")).toContainText("233.5 Token/s");
  await expect(conversationOverview.getByLabel("Token 生成速率")).toContainText("输出 Token/s");
  await expect(conversationOverview.getByLabel("Token 生成速率")).toContainText("42 ms");
  await expect(conversationOverview.getByLabel("交互信息")).toContainText("交互轮次");
  await expect(conversationOverview.getByLabel("交互信息")).toContainText("消息数");
  await expect(conversationOverview.getByLabel("上下文使用")).toContainText("128,000");
  await expect(conversationOverview.getByLabel("上下文使用")).toContainText("压缩次数");

  await expect(page.getByLabel("模型性能分析").getByText("debug-model", { exact: true })).toBeVisible();
  await expect(page.getByLabel("交互内容")).toHaveCount(0);

  await page.getByRole("tab", { name: "能力调用", exact: true }).click();
  await expect(page.getByLabel("能力调用分析").getByText("demo.lookup", { exact: true })).toBeVisible();

  await page.getByRole("tab", { name: "Span 调用树", exact: true }).click();
  const personalSpanTree = page.getByLabel("第 1 轮交互调用树");
  await expect(personalSpanTree).toBeVisible();
  await expect(page.getByLabel("第 1 轮调用树指标")).toContainText("输入 90 Token");
  await expect(page.getByLabel("第 1 轮调用树指标")).toContainText("输出 30 Token");
  await expect(page.getByLabel("第 1 轮调用树指标")).toContainText("时延 180 ms");
  await expect(personalSpanTree.getByText("用户输入", { exact: true })).not.toBeVisible();
  await personalSpanTree.getByText("第 1 轮交互", { exact: true }).click();
  await expect(personalSpanTree.getByText("用户输入", { exact: true })).toBeVisible();
  await expect(personalSpanTree.getByText("排查模型调用", { exact: true })).toBeVisible();
  await expect(personalSpanTree.getByText("模型输出", { exact: true })).toBeVisible();
  const toolCallNode = personalSpanTree.getByLabel("工具调用 demo.lookup", { exact: true });
  await expect(toolCallNode.getByText("工具调用", { exact: true })).toBeVisible();
  await expect(toolCallNode.getByText("调用参数", { exact: true })).toBeVisible();
  await expect(toolCallNode).toContainText("查询：");
  await expect(toolCallNode).toContainText("Unibot");
  await expect(toolCallNode.getByText("返回结果", { exact: true })).toBeVisible();
  await expect(toolCallNode.getByText("工具返回正常", { exact: true })).toBeVisible();
  await expect(toolCallNode).toContainText("v1.0.0");
  await expect(personalSpanTree.getByText("最终回复", { exact: true })).toBeVisible();
  await expect(personalSpanTree.getByText("模型返回正常", { exact: true })).toBeVisible();
  await expect(personalSpanTree.getByText("agent.run", { exact: true })).toHaveCount(0);
  await expect(personalSpanTree.getByLabel("model.complete 输入")).toHaveCount(0);
  await expect(personalSpanTree.getByText("null", { exact: true })).toHaveCount(0);
  await expect(personalSpanTree.locator("pre")).toHaveCount(0);

  await page.getByRole("tab", { name: "原始日志", exact: true }).click();
  await expect(page.getByLabel("原始日志角色筛选").getByRole("button", { name: "assistant", exact: true })).toBeVisible();
  const modelRawLogTitle = page.getByText("模型请求 1 · debug-model", { exact: true });
  const modelRawLog = modelRawLogTitle.locator("..").locator("..");
  await modelRawLogTitle.click();
  await expect(modelRawLog.getByText("原始输入", { exact: true })).toBeVisible();
  await expect(modelRawLog.getByText("原始输出", { exact: true })).toBeVisible();
  await expect(page.getByLabel("原始日志").getByText("null", { exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "个人总览", exact: true }).click();
  await expect(page.getByRole("button", { name: "日", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "周", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "月", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "自定义", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Token 消耗日历")).toBeVisible();

  await page.goto("/admin/observability");

  await page.getByRole("button", { name: "切换为管理员", exact: true }).click();
  await expect(page.getByRole("heading", { name: "可观测与质量", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "可观测", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "反馈", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "运营", exact: true })).toBeVisible();

  await page.getByRole("link", { name: "反馈", exact: true }).click();
  await expect(page.getByRole("heading", { name: "用户反馈", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "运营", exact: true }).click();
  await expect(page.getByRole("heading", { name: "运营增长", exact: true })).toBeVisible();
});

test("FE-E2E-IR-002 普通用户提交、修改和取消回答反馈", async ({ page }) => {
  await installMockApi(page, {
    conversations: [conversation({
      messages: [{
        id: "msg-feedback-e2e",
        role: "assistant",
        content: "这是可以评价的回答。",
        content_type: "text",
        widgets: [],
        created_at: NOW,
      }],
    })],
  });
  await page.goto("/chat/conv-e2e-1");

  const down = page.getByRole("button", { name: "点踩回答 msg-feedback-e2e", exact: true });
  await down.click();
  const dialog = page.getByRole("dialog", { name: "提交点踩反馈", exact: true });
  await dialog.getByRole("button", { name: "回答不完整", exact: true }).click();
  await dialog.getByRole("textbox", { name: "反馈补充说明", exact: true }).fill("缺少负责人联系方式。");
  await dialog.getByRole("button", { name: "提交反馈", exact: true }).click();
  await expect(down).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "取消评价 msg-feedback-e2e", exact: true })).toBeVisible();

  const up = page.getByRole("button", { name: "点赞回答 msg-feedback-e2e", exact: true });
  await up.click();
  await expect(up).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "取消评价 msg-feedback-e2e", exact: true }).click();
  await expect(up).toHaveAttribute("aria-pressed", "false");
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

test("FE-E2E-005B 流式回复进行中切换会话不会串线", async ({ page }) => {
  await installMockApi(page, {
    streamDelayMs: 300,
    conversations: [
      conversation({ id: "conv-streaming", title: "进行中的会话" }),
      conversation({
        id: "conv-other",
        title: "另一个会话",
        messages: [
          {
            id: "msg-other",
            role: "assistant",
            content: "这是另一个会话的独立消息。",
            content_type: "text",
            widgets: [],
            created_at: NOW,
          },
        ],
      }),
    ],
  });
  await page.goto("/chat/conv-streaming");
  const main = page.locator("main");

  await page.getByRole("textbox", { name: "消息", exact: true }).fill("只属于进行中会话的问题");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(main.getByText("只属于进行中会话的问题", { exact: true })).toBeVisible();

  await page.getByTestId("conversation-row-conv-other").click();
  await expect(page).toHaveURL(/\/chat\/conv-other$/);
  await expect(main.getByText("这是另一个会话的独立消息。", { exact: true })).toBeVisible();
  await expect(main.getByText("只属于进行中会话的问题", { exact: true })).toHaveCount(0);

  await page.waitForTimeout(450);
  await expect(page).toHaveURL(/\/chat\/conv-other$/);
  await expect(main.getByText("这是确定性的端到端回复。", { exact: true })).toHaveCount(0);

  await page.getByTestId("conversation-row-conv-streaming").click();
  await expect(page).toHaveURL(/\/chat\/conv-streaming$/);
  await expect(main.getByText("只属于进行中会话的问题", { exact: true })).toBeVisible();
  await expect(main.getByText("这是确定性的端到端回复。", { exact: true })).toBeVisible();
});

test("FE-E2E-005C Canvas 流式回复进行中切换 AINA 不会串线", async ({ page }) => {
  await installMockApi(page, {
    streamDelayMs: 300,
    conversations: [
      conversation({
        id: "conv-canvas-streaming",
        title: "Canvas 进行中的会话",
        messages: [
          {
            id: "msg-switch-aina",
            role: "assistant",
            content: "可以切换到记忆应用。",
            content_type: "text",
            widgets: [
              {
                id: "switch-to-memory",
                kind: "navigation",
                title: "切换应用",
                description: "继续在同一个会话中使用其他内置能力。",
                markdown: null,
                fields: [],
                apps: [],
                actions: [
                  {
                    id: "open-memory",
                    label: "切换到记忆",
                    kind: "open_aina",
                    aina_id: "unibot-memory",
                    style: "primary",
                  },
                ],
              },
            ],
            created_at: NOW,
          },
        ],
      }),
    ],
  });
  await page.goto("/canvas/unibot-documents?conversation=conv-canvas-streaming");

  await page.getByRole("textbox", { name: "画布消息" }).fill("只属于文档 Canvas 的问题");
  await page.getByRole("button", { name: "发送画布消息" }).click();
  await expect(page.getByText("只属于文档 Canvas 的问题", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "切换到记忆", exact: true }).click();
  await expect(page).toHaveURL(/\/canvas\/unibot-memory\?conversation=conv-canvas-streaming$/);
  await expect(page.getByText("只属于文档 Canvas 的问题", { exact: true })).toHaveCount(0);

  await page.waitForTimeout(450);
  await expect(page).toHaveURL(/\/canvas\/unibot-memory\?conversation=conv-canvas-streaming$/);
  await expect(page.getByText("这是确定性的端到端回复。", { exact: true })).toHaveCount(0);

  await page.goBack();
  await expect(page).toHaveURL(/\/canvas\/unibot-documents\?conversation=conv-canvas-streaming$/);
  await expect(page.getByText("只属于文档 Canvas 的问题", { exact: true })).toBeVisible();
  await expect(page.getByText("这是确定性的端到端回复。", { exact: true })).toBeVisible();
});

test("FE-E2E-006 应用列表 Widget 打开对应 Canvas", async ({ page }) => {
  const appListWidget = {
    id: "unibot-app-list",
    kind: "app_list",
    title: "AINA 应用",
    description: "当前共有 3 个可用应用。",
    markdown: null,
    fields: [],
    actions: [],
    apps: [
      {
        aina_id: "unibot-documents",
        name: "文档编辑器",
        description: "创建、读取和编辑 Markdown 文档。",
        version: "1.0.0",
        publisher: "Unibot",
        installed: true,
        has_main_widget: true,
      },
      {
        aina_id: "unibot-memory",
        name: "Unibot Memory",
        description: "管理长期记忆。",
        version: "1.0.0",
        publisher: "Unibot",
        installed: true,
        has_main_widget: true,
      },
      {
        aina_id: "unibot-scheduler",
        name: "定时任务 AINA",
        description: "通过固定间隔或 Cron 表达式调度远程 AINA。",
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
  await page.goto("/canvas/unibot-documents?conversation=conv-e2e-1");

  const documentApp = page.getByRole("button", { name: "打开 文档编辑器" });
  const memoryApp = page.getByRole("button", { name: "打开 Unibot Memory" });
  await expect(documentApp).toBeVisible();
  await expect(memoryApp).toBeVisible();
  const documentBox = await documentApp.boundingBox();
  const memoryBox = await memoryApp.boundingBox();
  expect(documentBox?.width).toBeGreaterThan(200);
  expect(memoryBox?.y).toBeGreaterThan((documentBox?.y ?? 0) + (documentBox?.height ?? 0));

  await memoryApp.click();

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
