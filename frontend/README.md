# World One V2 Frontend (天舟AI)

> React + Tailwind CSS implementation of the World One Agent Console V2 design (`frontend/new-design.pen`), with browser-side backend stubs (MSW).

## 快速开始

```bash
cd frontend/v2-app
npm install
npm run dev
```

打开 http://127.0.0.1:5173 — 默认进入 `/chat`。

## 技术栈

- **Vite 5** + **React 18** + **TypeScript 5**
- **Tailwind CSS 3** (主题色板匹配 `new-design.pen` 中的 slate / blue palette)
- **React Router 6** 多页面路由
- **MSW 2** 浏览器端接口打桩 (在 Service Worker 中拦截 `/api/*`)
- **lucide-react** 图标库

## 路由 → 设计稿对应

| 路由 | 设计稿 | 说明 |
|---|---|---|
| `/chat` | Page 01 | 聊天模式：双栏（侧边栏 + 对话流） |
| `/todo` | Page 01B | 待办模式：状态徽章变为"任务进行中" |
| `/system` | Page 01C | 聊天变体：展示系统交互 surface（confirm / status / error） |
| `/canvas` | Page 02 | 画布模式：3 栏（窄对话 + Memory widget） |
| `/settings` | Page 03 | 设置面板：模型 + 环境变量 + 连接状态 |
| `/apps` | Page 06 | 全部应用：应用卡片网格 |
| `/apps/memory` | Page 06A | Memory 应用：统计 + 筛选 + 列表 |

## 接口打桩（MSW）

所有后端调用都走 `/api/*`，由 `src/mocks/handlers.ts` 在浏览器内拦截并返回 seed 数据：

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/sessions` | 侧边栏会话列表（含 conversation / task / event / app 四种） |
| GET | `/api/sessions/:id/thread` | 会话消息流（`?kind=system` 返回系统交互变体） |
| POST | `/api/sessions/:id/messages` | 发送用户消息，返回 Agent 模拟回复 |
| POST | `/api/sessions/:id/sys/confirm` | 系统确认框（cancel / confirm） |
| GET | `/api/apps` | 应用列表（含系统 / 内置 / 扩展） |
| PATCH | `/api/apps/:id` | 切换应用启用状态 |
| GET | `/api/memories` | 记忆列表（支持 `?category=` / `?q=` 过滤） |
| GET | `/api/memories/stats` | 统计：total / fact / goal / preference / pending |
| POST | `/api/memories/:id/keep` | 保留记忆 |
| POST | `/api/memories/:id/delete` | 删除记忆 |
| GET | `/api/settings` | 模型服务商 + 环境变量 + 连接状态 |
| PATCH | `/api/settings/provider/:id` | 切换默认 Provider |
| POST | `/api/settings/test-connection` | 模拟测试连接（带延迟） |

替换为真实后端时：删除 `main.tsx` 中的 `enableMocking()` 调用 + 删除 `src/mocks/` 目录即可。

## 目录结构

```
frontend/v2-app/
├── public/
│   ├── favicon.svg
│   └── mockServiceWorker.js          # MSW Service Worker
├── src/
│   ├── App.tsx                       # 路由表
│   ├── main.tsx                      # 入口 + MSW 启动
│   ├── index.css                     # Tailwind 入口 + 自定义 utility
│   ├── types.ts                      # 全部领域类型
│   ├── lib/
│   │   ├── api.ts                    # fetch 封装
│   │   └── utils.ts                  # classNames / timeAgo
│   ├── mocks/
│   │   ├── browser.ts                # MSW worker 注册
│   │   ├── handlers.ts               # 全部接口 handler
│   │   └── seed.ts                   # 初始数据
│   ├── components/
│   │   ├── layout/
│   │   │   ├── AppShell.tsx
│   │   │   ├── Sidebar.tsx
│   │   │   └── Topbar.tsx
│   │   └── chat/
│   │       ├── MessageBubble.tsx     # UserMessage / AssistantMessage / ThinkingBubble / Composer
│   │       └── SurfaceRenderer.tsx   # 6 种 system surface 块
│   └── pages/
│       ├── ChatModePage.tsx
│       ├── TodoModePage.tsx
│       ├── ChatSystemInteractionPage.tsx
│       ├── CanvasModePage.tsx
│       ├── SettingsPage.tsx
│       ├── AllAppsPage.tsx
│       └── MemoryAppPage.tsx
├── tailwind.config.js                # 主题色板（slate + blue accent + warning/danger/success）
├── postcss.config.js
├── vite.config.ts                    # @ → src/ alias
├── tsconfig.json
├── tsconfig.node.json
└── package.json
```

## 关键设计点

- **6 种 system surface**：`choices`（应用选项）/ `confirm`（确认框）/ `status`（状态条）/ `error`（错误条）/ `loading`（加载）/ `form`（表单），由 `SurfaceRenderer` 统一渲染
- **暗色侧边栏**：slate-900，激活项 = blue-700 + 透明边框
- **统一主区**：白卡 + 灰底，Topbar 底边 1px slate-200
- **字体**：Inter (sans) + Geist (display) + Geist Mono（monospace），通过 Google Fonts 加载
- **响应式**：设计稿目标 1920×1080；侧边栏 232px 固定，主区弹性

## 常用命令

```bash
npm run dev       # 启动 dev server (HMR)
npm run build     # 生产构建 → dist/
npm run preview   # 预览生产构建
npm run lint      # 仅类型检查
```

## 与现有 Java 后端的衔接

世界一（world-one）和 memory-one 已经是 Spring Boot 后端。本前端要切到真实后端时：

1. `src/lib/api.ts` 中 `BASE` 改为真实 URL（可加环境变量 `VITE_API_BASE`）
2. 删除 `main.tsx` 中 `enableMocking()` + `src/mocks/`
3. 后端 Controller 路径对齐 MSW handler 即可（session/message/app/memory/settings）
