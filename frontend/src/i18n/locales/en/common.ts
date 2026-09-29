export default {
  time: {
    justNow: "Just now",
    minutesAgo: "{{count}} min ago",
    hoursAgo: "{{count}} hr ago",
    daysAgo: "{{count}} days ago",
  },
  error: {
    requestFailed: "Request failed: {{status}}",
    requestPathFailed: "Request {{path}} failed ({{status}})",
    retryLater: "Request failed. Please try again later.",
    noStream: "The browser did not provide a streaming response body.",
  },
  category: {
    general: "Uncategorized",
    work: "Work",
    personal: "Personal",
    project: "Project",
  },
  auth: {
    localUser: "Local user",
    checking: "Checking sign-in status…",
  },
  mock: {
    role: {
      user: "Standard user",
      admin: "Platform admin",
    },
    userName: "Alex Lin",
    adminName: "Ryan Zhou",
    tenant: "Tianzhou Tech",
    userInitials: "A",
    adminInitials: "R",
  },
  workspace: {
    userSwitched: "The current user has changed. Please create the workspace again.",
  },
  admin: {
    observability: "Observability",
    feedback: "Feedback",
    operations: "Operations",
  },
  forbidden: {
    eyebrow: "403 · Permission check",
    title: "Administrator access required",
    body: "Standard users can only access their own conversations and feedback. Platform observability, feedback handling, and operations data are shown to administrators only.",
    back: "Back to chat",
    switch: "Switch to admin",
    mockHint: "Auth is not enabled locally; you can switch the mock identity to test this page.",
    realHint: "Admin access is determined by the backend sign-in identity and allowlist.",
  },
  mockData: "Mock data",
  trendChart: "{{label}} trend chart",
  system: {
    title: "System interaction status",
    ready: "Ready",
  },
  todo: {
    inProgress: "Task in progress",
  },
  widget: {
    openApp: "Open {{name}}",
    noApps: "No AINA apps are currently available.",
    loadingEditor: "Loading code editor…",
    loadingImage: "Loading image recognition…",
  },
  language: "Language",
};
