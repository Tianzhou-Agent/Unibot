/**
 * OBS 数据加载（设计迁移后）：
 * 个人总览、对话详情与原始日志全部走后端聚合 DTO，
 * 前端不再循环分页加载用户的全部 LLM Call 后自行聚合。
 * 具体接口封装见 ./obsApi。
 */
export { getAdminObsSession, getObsOverview, getObsSession, getRawLogs } from "@/lib/obsApi";
export type {
  ObsEvent,
  ObsOverview,
  ObsRawLog,
  ObsSessionDetail,
  ObsSpan,
  ObsTrace,
} from "@/lib/obsApi";
