export interface TrendPoint {
  label: string;
  requests: number;
  errors: number;
  activeUsers: number;
  feedbackRate: number;
  positiveRate: number;
}

export interface TraceSummaryMock {
  id: string;
  request: string;
  agent: string;
  version: string;
  status: "completed" | "failed";
  duration: string;
  spans: number;
  tokens: string;
  createdAt: string;
}

export interface ErrorSummaryMock {
  id: string;
  severity: "high" | "medium" | "low";
  code: string;
  source: string;
  target: string;
  message: string;
  count: number;
  traceId: string;
  spanId: string;
  lastSeen: string;
}

export interface PerformanceRowMock {
  name: string;
  detail: string;
  calls: string;
  successRate: number;
  p50: string;
  p95: string;
  p99: string;
  tokens: string;
}

export interface FeedbackHistoryMock {
  at: string;
  actor: string;
  action: string;
}

export interface FeedbackRecordMock {
  id: string;
  user: string;
  agent: string;
  version: string;
  rating: "up" | "down";
  reason: string;
  comment: string;
  status: "待处理" | "处理中" | "已解决" | "已关闭";
  assignee: string;
  conclusion: string;
  conversationId: string;
  messageId: string;
  traceId: string | null;
  createdAt: string;
  history: FeedbackHistoryMock[];
}

export interface AgentAdoptionMock {
  agent: string;
  eligibleUsers: number;
  activeUsers: number;
  penetration: number;
  requests: string;
  positiveRate: number;
  d7: number;
}

export interface CohortRowMock {
  cohort: string;
  users: number;
  retention: Array<number | null>;
}

export const METRIC_CONTEXT = {
  version: "v1.0-mock",
  timezone: "Asia/Shanghai",
  window: "最近 7 个自然日",
  asOf: "2026-08-03 15:40",
};

export const TREND_POINTS: TrendPoint[] = [
  { label: "07/28", requests: 1420, errors: 48, activeUsers: 736, feedbackRate: 18.4, positiveRate: 84.2 },
  { label: "07/29", requests: 1680, errors: 61, activeUsers: 802, feedbackRate: 19.1, positiveRate: 85.5 },
  { label: "07/30", requests: 1540, errors: 43, activeUsers: 781, feedbackRate: 20.3, positiveRate: 86.1 },
  { label: "07/31", requests: 1890, errors: 72, activeUsers: 846, feedbackRate: 21.8, positiveRate: 83.7 },
  { label: "08/01", requests: 2050, errors: 67, activeUsers: 901, feedbackRate: 23.4, positiveRate: 87.2 },
  { label: "08/02", requests: 1980, errors: 54, activeUsers: 884, feedbackRate: 22.7, positiveRate: 88.1 },
  { label: "08/03", requests: 2282, errors: 49, activeUsers: 926, feedbackRate: 24.6, positiveRate: 89.4 },
];

export const TRACE_SUMMARIES: TraceSummaryMock[] = [
  { id: "trace_mock_8f32a1", request: "分析 Q3 用户反馈并生成摘要", agent: "数据分析助手", version: "v2.4.1", status: "completed", duration: "3.42 s", spans: 7, tokens: "4.8k", createdAt: "15:36:18" },
  { id: "trace_mock_7c918e", request: "读取采购合同并标注风险条款", agent: "文档助手", version: "v1.9.0", status: "failed", duration: "8.91 s", spans: 11, tokens: "6.2k", createdAt: "15:31:02" },
  { id: "trace_mock_6ba40d", request: "查询本周逾期任务并通知负责人", agent: "任务助手", version: "v3.1.2", status: "completed", duration: "2.17 s", spans: 5, tokens: "2.1k", createdAt: "15:24:46" },
  { id: "trace_mock_5e27bf", request: "对比两个模型版本的回答质量", agent: "模型评测助手", version: "v1.3.5", status: "completed", duration: "5.68 s", spans: 9, tokens: "8.7k", createdAt: "15:18:09" },
  { id: "trace_mock_49c120", request: "从知识库检索产品退款规则", agent: "客服助手", version: "v4.0.0", status: "failed", duration: "6.33 s", spans: 8, tokens: "3.4k", createdAt: "15:09:27" },
];

export const ERROR_SUMMARIES: ErrorSummaryMock[] = [
  { id: "err-001", severity: "high", code: "TOOL_TIMEOUT", source: "Tool", target: "contract-parser", message: "合同解析超过 8 秒执行上限", count: 23, traceId: "trace_mock_7c918e", spanId: "span_tool_04", lastSeen: "4 分钟前" },
  { id: "err-002", severity: "medium", code: "MODEL_RATE_LIMITED", source: "Model", target: "deepseek-chat", message: "Provider 返回请求频率限制", count: 17, traceId: "trace_mock_49c120", spanId: "span_model_02", lastSeen: "11 分钟前" },
  { id: "err-003", severity: "medium", code: "INVALID_TOOL_RESULT", source: "AINA", target: "knowledge-search", message: "工具结果缺少必需字段 documents", count: 9, traceId: "trace_mock_32af90", spanId: "span_tool_06", lastSeen: "28 分钟前" },
  { id: "err-004", severity: "low", code: "CONTEXT_TRUNCATED", source: "Agent", target: "data-analyst", message: "输入上下文超过预算并被截断", count: 31, traceId: "trace_mock_11fe42", spanId: "span_agent_01", lastSeen: "36 分钟前" },
];

export const PERFORMANCE: Record<"agent" | "model" | "tool", PerformanceRowMock[]> = {
  agent: [
    { name: "数据分析助手", detail: "v2.4.1", calls: "3,842", successRate: 98.2, p50: "2.1 s", p95: "5.8 s", p99: "9.3 s", tokens: "3.8k" },
    { name: "文档助手", detail: "v1.9.0", calls: "2,916", successRate: 94.7, p50: "3.4 s", p95: "8.9 s", p99: "14.2 s", tokens: "5.1k" },
    { name: "客服助手", detail: "v4.0.0", calls: "2,408", successRate: 96.4, p50: "1.8 s", p95: "4.6 s", p99: "7.1 s", tokens: "2.7k" },
    { name: "任务助手", detail: "v3.1.2", calls: "1,663", successRate: 99.1, p50: "1.2 s", p95: "2.9 s", p99: "4.5 s", tokens: "1.6k" },
  ],
  model: [
    { name: "deepseek-chat", detail: "DeepSeek · primary", calls: "7,481", successRate: 97.6, p50: "1.7 s", p95: "4.2 s", p99: "7.8 s", tokens: "3.9k" },
    { name: "gpt-5-mini", detail: "OpenAI · fallback", calls: "2,938", successRate: 99.0, p50: "2.2 s", p95: "5.4 s", p99: "8.1 s", tokens: "4.3k" },
    { name: "qwen3-32b", detail: "Private cloud", calls: "410", successRate: 92.8, p50: "3.8 s", p95: "9.7 s", p99: "16.4 s", tokens: "3.1k" },
  ],
  tool: [
    { name: "knowledge-search", detail: "AINA Tool", calls: "4,206", successRate: 97.1, p50: "380 ms", p95: "1.8 s", p99: "3.4 s", tokens: "—" },
    { name: "contract-parser", detail: "Managed Tool", calls: "1,184", successRate: 91.6, p50: "2.9 s", p95: "7.8 s", p99: "12.1 s", tokens: "—" },
    { name: "task-query", detail: "Builtin Tool", calls: "2,781", successRate: 99.4, p50: "140 ms", p95: "420 ms", p99: "880 ms", tokens: "—" },
    { name: "document-read", detail: "Builtin Tool", calls: "3,029", successRate: 98.9, p50: "210 ms", p95: "690 ms", p99: "1.2 s", tokens: "—" },
  ],
};

export const FEEDBACK_REASON_DISTRIBUTION = [
  { label: "事实或结论错误", value: 34 },
  { label: "未理解用户意图", value: 25 },
  { label: "回答不完整", value: 19 },
  { label: "响应速度慢", value: 13 },
  { label: "工具结果错误", value: 9 },
];

export const FEEDBACK_RECORDS: FeedbackRecordMock[] = [
  {
    id: "fb-20260803-001", user: "陈雨", agent: "文档助手", version: "v1.9.0", rating: "down", reason: "工具结果错误",
    comment: "合同第 8 条的违约金额识别错了，原文是 20 万，不是 200 万。", status: "处理中", assignee: "王珂", conclusion: "",
    conversationId: "conv_mock_contract", messageId: "msg_mock_0042", traceId: "trace_mock_7c918e", createdAt: "2026-08-03 15:32",
    history: [
      { at: "15:32", actor: "陈雨", action: "提交点踩：工具结果错误" },
      { at: "15:38", actor: "周然", action: "分配给王珂并开始处理" },
    ],
  },
  {
    id: "fb-20260803-002", user: "赵宁", agent: "客服助手", version: "v4.0.0", rating: "down", reason: "事实或结论错误",
    comment: "退款时限引用了去年的规则，需要使用 2026 年 7 月更新版本。", status: "待处理", assignee: "未分配", conclusion: "",
    conversationId: "conv_mock_refund", messageId: "msg_mock_0037", traceId: "trace_mock_49c120", createdAt: "2026-08-03 15:11",
    history: [{ at: "15:11", actor: "赵宁", action: "提交点踩：事实或结论错误" }],
  },
  {
    id: "fb-20260803-003", user: "匿名用户 5A9C", agent: "数据分析助手", version: "v2.4.1", rating: "up", reason: "分析清晰",
    comment: "图表解释很直观，尤其是异常点的说明。", status: "已关闭", assignee: "系统", conclusion: "正向反馈，无需处理",
    conversationId: "conv_mock_analysis", messageId: "msg_mock_0031", traceId: "trace_mock_8f32a1", createdAt: "2026-08-03 14:56",
    history: [{ at: "14:56", actor: "匿名用户 5A9C", action: "提交点赞：分析清晰" }],
  },
  {
    id: "fb-20260803-004", user: "林晨", agent: "任务助手", version: "v3.1.2", rating: "down", reason: "回答不完整",
    comment: "只列出了逾期任务，没有带上负责人联系方式。", status: "已解决", assignee: "李牧", conclusion: "已补充负责人字段并在 v3.1.3 发布",
    conversationId: "conv_mock_tasks", messageId: "msg_mock_0022", traceId: "trace_mock_6ba40d", createdAt: "2026-08-03 13:40",
    history: [
      { at: "13:40", actor: "林晨", action: "提交点踩：回答不完整" },
      { at: "13:52", actor: "李牧", action: "状态更新为处理中" },
      { at: "14:31", actor: "李牧", action: "记录处理结论并解决" },
    ],
  },
  {
    id: "fb-20260802-018", user: "宋羽", agent: "模型评测助手", version: "v1.3.5", rating: "up", reason: "结果准确",
    comment: "版本对比结论和人工抽检一致。", status: "已关闭", assignee: "系统", conclusion: "正向反馈，无需处理",
    conversationId: "conv_mock_eval", messageId: "msg_mock_0018", traceId: "trace_mock_5e27bf", createdAt: "2026-08-02 18:22",
    history: [{ at: "18:22", actor: "宋羽", action: "提交点赞：结果准确" }],
  },
];

export const AGENT_ADOPTION: AgentAdoptionMock[] = [
  { agent: "数据分析助手", eligibleUsers: 680, activeUsers: 492, penetration: 72.4, requests: "3,842", positiveRate: 91.8, d7: 66.2 },
  { agent: "客服助手", eligibleUsers: 520, activeUsers: 314, penetration: 60.4, requests: "2,408", positiveRate: 86.4, d7: 58.7 },
  { agent: "任务助手", eligibleUsers: 910, activeUsers: 421, penetration: 46.3, requests: "1,663", positiveRate: 89.1, d7: 52.5 },
  { agent: "文档助手", eligibleUsers: 760, activeUsers: 286, penetration: 37.6, requests: "2,916", positiveRate: 78.9, d7: 41.8 },
  { agent: "模型评测助手", eligibleUsers: 180, activeUsers: 43, penetration: 23.9, requests: "410", positiveRate: 88.3, d7: 35.4 },
];

export const COHORT_ROWS: CohortRowMock[] = [
  { cohort: "07/06–07/12", users: 318, retention: [100, 63, 51, 46, 42] },
  { cohort: "07/13–07/19", users: 346, retention: [100, 66, 54, 48, null] },
  { cohort: "07/20–07/26", users: 391, retention: [100, 68, 57, null, null] },
  { cohort: "07/27–08/02", users: 428, retention: [100, 71, null, null, null] },
];
