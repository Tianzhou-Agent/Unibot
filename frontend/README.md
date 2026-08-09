# Unibot Frontend

React 18 + Vite 5 + TypeScript 前端，默认连接真实 FastAPI 后端。

## 启动

```bash
cd frontend
corepack enable && corepack prepare pnpm@9.15.9 --activate
pnpm install --frozen-lockfile
pnpm dev
```

默认地址为 `http://127.0.0.1:5173`，Vite 会将 `/api` 代理到
`http://127.0.0.1:8000`。可通过 `VITE_API_BASE_URL` 指向其他后端；只有显式设置
`VITE_ENABLE_MOCKS=true` 时才启用 MSW。

## 当前路由

| 路由 | 功能 |
| --- | --- |
| `/chat`、`/chat/:conversationId` | 可恢复流式对话、Markdown、Widget、授权、分类与删除 |
| `/canvas/:ainaId` | 当前 AINA 对话框 + `main_widget` 双栏工作区 |
| `/apps` | AINA、Tool、Skill 注册与 AINA 安装/打开 |
| `/settings` | 健康状态、管理摘要和 Trace 查看 |

## Widget

前端通过宿主 Widget 注册表统一渲染 AINA Protocol 中的 `app_list`、`form`、`markdown`、`panel`、`memory` 与
`navigation` Widget。应用列表卡片会先调用后端 `open_aina`，再进入 Canvas；表单
Action 会把字段插值为 Prompt，并路由给当前 AINA。Markdown 使用 `react-markdown`
和 GFM 插件，原始 HTML 不会执行。

AINA 通过 `capabilities.ui` 声明需要的 UI 类型、用途和使用说明，无需提交 AINA 专用前端代码。
模糊需求可以调用 `request_clarification` 生成通用表单，并支持模型预填已知字段。

Debug 模式默认关闭。关闭时不会展示 Tool 调用、原始参数、模型迭代、Token 或 Trace；可在运行中心开启。

## 校验

```bash
pnpm build
```
