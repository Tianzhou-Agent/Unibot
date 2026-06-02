import type {
  AppDescriptor,
  ChatThread,
  EnvVar,
  MemoryItem,
  MemoryStats,
  ModelProvider,
  ConnectionStatus,
  SessionSummary,
  SettingsResponse,
} from "@/types";

const now = new Date();
const iso = (offsetMin: number) => new Date(now.getTime() - offsetMin * 60_000).toISOString();

export const SESSIONS: SessionSummary[] = [
  {
    id: "sess_canvas_app",
    title: "询问Canvas应用详情",
    kind: "conversation",
    status: "active",
    preview: "当前可用应用如下…",
    updatedAt: iso(2),
    pinned: true,
  },
  {
    id: "sess_memory_audit",
    title: "记忆审计",
    kind: "task",
    status: "pending",
    preview: "正在扫描 workspace 范围内的语义记忆…",
    updatedAt: iso(11),
  },
  {
    id: "sess_canvas_review",
    title: "画布关系复盘",
    kind: "task",
    status: "done",
    preview: "对比 canvas context 与 ontology session 的关联…",
    updatedAt: iso(34),
  },
];

const fileChip = {
  id: "file_app_manifest",
  name: "app-manifest.json",
  sizeBytes: 4382,
  mimeType: "application/json",
};

const choiceMemory = {
  id: "opt_memory",
  title: "记忆管理",
  description: "查看与筛选",
  icon: "database",
  iconTone: "blue" as const,
};
const choiceUpload = {
  id: "opt_upload",
  title: "文件上传",
  description: "PDF / DOCX / 图片",
  icon: "upload",
  iconTone: "blue" as const,
};
const choiceCanvas = {
  id: "opt_canvas",
  title: "画布应用",
  description: "打开应用工作区",
  icon: "layout-dashboard",
  iconTone: "green" as const,
};

export const CHAT_THREAD_CHAT_MODE: ChatThread = {
  sessionId: "sess_canvas_app",
  title: "询问 Canvas 应用详情",
  messages: [
    {
      id: "m1",
      role: "user",
      content: "这个工作区当前有哪些记忆和上下文？",
      createdAt: iso(8),
    },
    {
      id: "m2",
      role: "assistant",
      content:
        "当前工作区包含 18 条语义记忆、7 个关系节点和 2 个活跃上下文。最相关的是 workspace memory、canvas context 和当前 ontology session。",
      createdAt: iso(7),
      runState: "done",
    },
    {
      id: "m3",
      role: "user",
      content: "当前有哪些应用可以用？",
      createdAt: iso(6),
    },
    {
      id: "m4",
      role: "assistant",
      content:
        "当前可用应用如下。上传文件会成为本轮对话上下文，选择应用则会在消息流中打开。",
      surface: {
        kind: "choices",
        options: [choiceMemory, choiceUpload, choiceCanvas],
      },
      createdAt: iso(5),
      runState: "done",
    },
    {
      id: "m5",
      role: "user",
      content: "参考我刚上传的 notes.md，查询 canvas context 相关记忆。",
      files: [fileChip],
      createdAt: iso(3),
    },
    {
      id: "m6",
      role: "assistant",
      content:
        "我会调用关系查询工具，查找 canvas context 和工作区记忆之间的连接。工具结果会作为这条回答的一部分展示。",
      blocks: [
        {
          kind: "result",
          text: "查询结果显示，canvas context 与 workspace memory、ontology session、用户目标三类记忆存在直接关系；附件仅作为本轮上下文。",
        },
      ],
      createdAt: iso(2),
      runState: "done",
    },
    {
      id: "m7",
      role: "user",
      content: "参考我刚上传的 notes.md，查询 canvas context 相关记忆。",
      files: [fileChip],
      createdAt: iso(1),
    },
  ],
};

export const CHAT_THREAD_TODO_MODE: ChatThread = {
  sessionId: "sess_sr_add",
  title: "在 SR12346566 下添加 AR",
  messages: [
    {
      id: "tm1",
      role: "user",
      content: "在 SR12346566 下添加一个新的需求",
      createdAt: iso(7),
    },
    {
      id: "tm2",
      role: "assistant",
      content: "已加载天舟需求应用",
      surface: { kind: "status", text: "已加载天舟需求应用", tone: "success" },
      createdAt: iso(6),
    },
    {
      id: "tm3",
      role: "assistant",
      content:
        "没有找到 SR12346566，找到几个相似的需求编号，请确认是否是以下几个：",
      surface: {
        kind: "choices",
        options: [
          { ...choiceMemory, id: "opt_sr_1", title: "SR1234556", description: "天舟需求 1", icon: "dock", iconTone: "blue", selected: true },
          { id: "opt_sr_2", title: "SR1334556", description: "天舟需求 2", icon: "dock", iconTone: "blue" },
          { id: "opt_sr_3", title: "SR1434556", description: "天舟需求 3", icon: "dock", iconTone: "blue" },
        ],
      },
      createdAt: iso(5),
    },
    {
      id: "tm4",
      role: "assistant",
      content: "用户确认是需求编号 SR1234556，我需要更多信息来创建",
      surface: {
        kind: "form",
        title: "补充 SR1234556 创建信息",
        hint: "请补充 3 项关键信息",
        submitLabel: "提交创建",
        cancelLabel: "取消",
        fields: [
          { id: "name", label: "需求名称 / 目标", placeholder: "例如：创建客户资料维护页面" },
          { id: "detail", label: "需求说明", placeholder: "补充业务背景、主要流程或验收标准" },
          { id: "owner", label: "负责人 / 截止时间", placeholder: "例如：张三，6 月 3 日前" },
        ],
      },
      createdAt: iso(3),
    },
  ],
};

export const CHAT_THREAD_SYSTEM_INTERACTION: ChatThread = {
  sessionId: "sess_canvas_app",
  title: "系统交互状态",
  messages: [
    {
      id: "si1",
      role: "user",
      content: "当前有哪些应用可以用？",
      createdAt: iso(8),
    },
    {
      id: "si2",
      role: "assistant",
      content:
        "当前可用应用如下。选择一个应用后，我会把它作为本轮对话的一部分打开，并保留在消息上下文里。",
      surface: { kind: "choices", options: [choiceMemory, choiceUpload, choiceCanvas] },
      createdAt: iso(7),
    },
    {
      id: "si3",
      role: "assistant",
      content: "",
      surface: {
        kind: "confirm",
        title: "确认打开记忆管理器？",
        description: "打开后会进入画布模式，并保留当前对话。",
        confirmLabel: "打开",
        cancelLabel: "取消",
        tone: "warning",
      },
      createdAt: iso(5),
    },
    {
      id: "si4",
      role: "assistant",
      content: "",
      surface: { kind: "status", text: "正在加载应用清单…", tone: "info" },
      createdAt: iso(4),
    },
    {
      id: "si5",
      role: "assistant",
      content: "",
      surface: { kind: "error", text: "应用加载失败：请检查 manifest 或网络状态。" },
      createdAt: iso(3),
    },
  ],
};

export const APPS: AppDescriptor[] = [
  {
    id: "app_memory",
    name: "Memory",
    description: "memory-one 注册应用；从 Page 07 全部应用选择后，在 Canvas 中打开 memory-manager 主模块。",
    icon: "database",
    tone: "blue",
    category: "extension",
    enabled: true,
    highlight: true,
    routesTo: "/apps/memory",
  },
  {
    id: "app_files",
    name: "文件解析",
    description: "上传本地文件并交给 Agent 解析，适合文档、表格和运行日志。",
    icon: "upload",
    tone: "blue",
    category: "extension",
    enabled: true,
  },
  {
    id: "app_events",
    name: "待处理事件",
    description:
      "事件不是独立应用，而是 Agent 工具链中断后的待处理入口；从 Page 04 进入后补参数并恢复原会话。",
    icon: "calendar-clock",
    tone: "blue",
    category: "system",
    enabled: true,
  },
  {
    id: "app_tasks",
    name: "任务会话",
    description: "任务由 Agent 派生，不手动新建；从 Page 06 进入后查看独立上下文、恢复 Canvas、完成或作废。",
    icon: "list-todo",
    tone: "blue",
    category: "system",
    enabled: true,
  },
  {
    id: "app_calendar",
    name: "日程应用",
    description: "查看本周日程并预约会议室。",
    icon: "calendar",
    tone: "green",
    category: "extension",
    enabled: false,
  },
  {
    id: "app_drawing",
    name: "画板",
    description: "轻量白板工具，Agent 可以在对话中发起草图协作。",
    icon: "pen-tool",
    tone: "indigo",
    category: "extension",
    enabled: false,
  },
];

export const MEMORY_STATS: MemoryStats = {
  total: 42,
  fact: 18,
  goal: 9,
  preference: 11,
  pending: 4,
};

export const MEMORY_ITEMS: MemoryItem[] = [
  {
    id: "mem_1",
    title: "World One 是本体 Agent 主机",
    meta: "事实 · workspace · 置信度 0.92 · 今天更新",
    source: "来源：Page 1 对话 / Agent State",
    sourceTone: "accent",
    actions: ["keep", "delete"],
    category: "fact",
    scope: "workspace",
    confidence: 0.92,
    updatedLabel: "今天更新",
    selected: true,
  },
  {
    id: "mem_2",
    title: "用户希望对 Canvas 与记忆关系保存摘要",
    meta: "目标 · workspace · 置信度 0.68 · 待确认",
    source: "来源：Canvas Mode 操作记录",
    sourceTone: "muted",
    actions: ["keep", "delete"],
    category: "goal",
    scope: "workspace",
    confidence: 0.68,
    updatedLabel: "待确认",
  },
  {
    id: "mem_3",
    title: "用户常用 DeepSeek 作为模型服务商",
    meta: "偏好 · global · 置信度 0.81",
    source: "来源：设置面板连接记录",
    sourceTone: "muted",
    actions: ["keep", "delete"],
    category: "preference",
    scope: "global",
    confidence: 0.81,
    updatedLabel: "5 天前",
  },
  {
    id: "mem_4",
    title: "未确认：是否在 ontology session 内启用 AR 视图",
    meta: "目标 · workspace · 置信度 0.42 · 待确认",
    source: "来源：SR 任务对话",
    sourceTone: "warning",
    actions: ["keep", "delete"],
    category: "pending",
    scope: "workspace",
    confidence: 0.42,
    updatedLabel: "1 小时前",
  },
  {
    id: "mem_5",
    title: "World One 默认 LLM Provider = system default",
    meta: "事实 · global · 置信度 0.95",
    source: "来源：设置面板连接记录",
    sourceTone: "muted",
    actions: ["keep", "delete"],
    category: "fact",
    scope: "global",
    confidence: 0.95,
    updatedLabel: "昨天",
  },
];

export const PROVIDER_DEEPSEEK: ModelProvider = {
  id: "prov_deepseek",
  name: "DeepSeek（推荐）",
  recommended: true,
  baseUrl: "https://api.deepseek.com/v1",
  model: "deepseek-chat",
  apiKeyMasked: "••••••••••••••••••••••••",
  timeoutSec: 120,
};

export const PROVIDER_OPENAI: ModelProvider = {
  id: "prov_openai",
  name: "OpenAI",
  baseUrl: "https://api.openai.com/v1",
  model: "gpt-4o-mini",
  apiKeyMasked: "••••••••••••••",
  timeoutSec: 120,
};

export const PROVIDER_OLLAMA: ModelProvider = {
  id: "prov_ollama",
  name: "Ollama (本地)",
  baseUrl: "http://localhost:11434/v1",
  model: "llama3.1",
  apiKeyMasked: "（无需密钥）",
  timeoutSec: 180,
};

export const ENV_VARS: EnvVar[] = [
  { key: "OPENAI_API_KEY", value: "全局", scope: "global" },
  { key: "APP_ENV", value: "工作区覆盖", scope: "workspace" },
];

export const CONNECTION: ConnectionStatus = {
  state: "connected",
  testedAt: "2 分钟前测试通过",
  statusCode: 200,
  latencyMs: 426,
  note: "deepseek-chat 可用",
};

export const SETTINGS: SettingsResponse = {
  providers: [PROVIDER_DEEPSEEK, PROVIDER_OPENAI, PROVIDER_OLLAMA],
  selectedProviderId: PROVIDER_DEEPSEEK.id,
  env: ENV_VARS,
  connection: CONNECTION,
};
