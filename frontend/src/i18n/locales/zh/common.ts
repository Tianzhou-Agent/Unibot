export default {
  time: {
    justNow: "刚刚",
    minutesAgo: "{{count}} 分钟前",
    hoursAgo: "{{count}} 小时前",
    daysAgo: "{{count}} 天前",
  },
  error: {
    requestFailed: "请求失败：{{status}}",
    requestPathFailed: "请求 {{path}} 失败（{{status}}）",
    retryLater: "请求失败，请稍后重试。",
    noStream: "浏览器未提供流式响应体。",
  },
  category: {
    general: "未分类",
    work: "工作",
    personal: "个人",
    project: "项目",
  },
  auth: {
    localUser: "本地用户",
    checking: "正在检查登录状态…",
  },
  mock: {
    role: {
      user: "普通用户",
      admin: "平台管理员",
    },
    userName: "林晨",
    adminName: "周然",
    tenant: "天舟科技",
    userInitials: "林",
    adminInitials: "周",
  },
  workspace: {
    userSwitched: "当前用户已切换，请重新创建工作区。",
  },
  admin: {
    observability: "可观测",
    feedback: "反馈",
    operations: "运营",
  },
  forbidden: {
    eyebrow: "403 · 权限校验",
    title: "需要管理员权限",
    body: "普通用户只能访问自己的对话和反馈入口，平台观测、反馈处理及运营数据仅对管理员展示。",
    back: "返回聊天",
    switch: "切换为管理员",
    mockHint: "本地未启用鉴权，可切换 Mock 身份验证页面。",
    realHint: "管理员权限由后端登录身份和授权名单决定。",
  },
  mockData: "Mock 数据",
  trendChart: "{{label}}趋势图",
  system: {
    title: "系统交互状态",
    ready: "已就绪",
  },
  todo: {
    inProgress: "任务进行中",
  },
  widget: {
    openApp: "打开 {{name}}",
    noApps: "当前没有可用的 AINA 应用。",
    loadingEditor: "正在加载代码编辑器…",
    loadingImage: "正在加载图片识别…",
  },
  language: "语言",
};
