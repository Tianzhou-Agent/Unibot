import { http, HttpResponse, delay } from "msw";
import {
  APPS,
  CHAT_THREAD_CHAT_MODE,
  CHAT_THREAD_SYSTEM_INTERACTION,
  CHAT_THREAD_TODO_MODE,
  MEMORY_ITEMS,
  MEMORY_STATS,
  SETTINGS,
  SESSIONS,
} from "./seed";
import type { ChatMessage, ChoiceOption, MemoryItem } from "@/types";
import { uid } from "@/lib/utils";

const BASE = "/api";

export const handlers = [
  http.get(`${BASE}/sessions`, async () => {
    await delay(80);
    return HttpResponse.json({ items: SESSIONS });
  }),

  http.get(`${BASE}/sessions/:id/thread`, async ({ params, request }) => {
    await delay(60);
    const id = params.id as string;
    const url = new URL(request.url);
    const kind = url.searchParams.get("kind");
    if (id === "sess_sr_add") return HttpResponse.json(CHAT_THREAD_TODO_MODE);
    if (id === "sess_canvas_app" && kind === "system")
      return HttpResponse.json(CHAT_THREAD_SYSTEM_INTERACTION);
    return HttpResponse.json(CHAT_THREAD_CHAT_MODE);
  }),

  http.post(`${BASE}/sessions/:id/messages`, async ({ params, request }) => {
    const body = (await request.json()) as { content: string; choiceId?: string };
    const reply: ChatMessage = {
      id: uid("m"),
      role: "assistant",
      content: echoReply(body.content, body.choiceId),
      createdAt: new Date().toISOString(),
      runState: "done",
    };
    await delay(420);
    return HttpResponse.json({ message: reply });
  }),

  http.post(`${BASE}/sessions/:id/sys/confirm`, async () => {
    await delay(180);
    return HttpResponse.json({ ok: true });
  }),

  http.get(`${BASE}/apps`, async () => {
    await delay(60);
    return HttpResponse.json({ items: APPS });
  }),

  http.patch(`${BASE}/apps/:id`, async ({ params, request }) => {
    const body = (await request.json()) as { enabled?: boolean };
    const app = APPS.find((a) => a.id === params.id);
    if (!app) return new HttpResponse(null, { status: 404 });
    if (typeof body.enabled === "boolean") app.enabled = body.enabled;
    return HttpResponse.json({ app });
  }),

  http.get(`${BASE}/memories/stats`, async () => {
    await delay(40);
    return HttpResponse.json(MEMORY_STATS);
  }),

  http.get(`${BASE}/memories`, async ({ request }) => {
    await delay(60);
    const url = new URL(request.url);
    const category = url.searchParams.get("category");
    const q = url.searchParams.get("q")?.toLowerCase() ?? "";
    let items: MemoryItem[] = MEMORY_ITEMS;
    if (category && category !== "all") items = items.filter((m) => m.category === category);
    if (q) items = items.filter((m) => m.title.toLowerCase().includes(q));
    return HttpResponse.json({ items, total: items.length });
  }),

  http.post(`${BASE}/memories/:id/keep`, async ({ params }) => {
    await delay(120);
    return HttpResponse.json({ ok: true, id: params.id });
  }),

  http.post(`${BASE}/memories/:id/delete`, async ({ params }) => {
    await delay(120);
    return HttpResponse.json({ ok: true, id: params.id });
  }),

  http.get(`${BASE}/settings`, async () => {
    await delay(60);
    return HttpResponse.json(SETTINGS);
  }),

  http.patch(`${BASE}/settings/provider/:id`, async ({ params }) => {
    await delay(80);
    SETTINGS.selectedProviderId = params.id as string;
    return HttpResponse.json({ ok: true, selectedProviderId: SETTINGS.selectedProviderId });
  }),

  http.post(`${BASE}/settings/test-connection`, async () => {
    await delay(800);
    return HttpResponse.json({
      state: "connected",
      testedAt: "刚刚测试通过",
      statusCode: 200,
      latencyMs: 380 + Math.floor(Math.random() * 120),
      note: "deepseek-chat 可用",
    });
  }),
];

function echoReply(content: string, choiceId?: string): string {
  if (choiceId) {
    return `已记录你选择的操作（${choiceId}）。Agent 正在调用对应工具，结果将出现在下一条消息。`;
  }
  if (content.includes("记忆"))
    return "已扫描工作区：18 条语义记忆、7 个关系节点、2 个活跃上下文。最相关的是 workspace memory、canvas context 与 ontology session。";
  if (content.includes("应用") || content.includes("画布"))
    return "可用应用：Memory、文件解析、待处理事件、任务会话。选择应用后会进入对应工作台。";
  return "收到。让我先调用相应工具再回复你。";
}
