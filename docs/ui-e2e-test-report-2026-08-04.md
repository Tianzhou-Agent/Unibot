# Unibot 前端 UI 深度 E2E 测试问题报告

- **测试时间**: 2026-08-04 14:50–15:15 (UTC+8)
- **测试方式**: Playwright 真实浏览器交互(非 MSW mock), 直接访问运行中的前端 `http://127.0.0.1:5173`(Vite dev)与后端 `http://127.0.0.1:8000`(FastAPI, 真实 DeepSeek LLM)
- **测试账号**: 新注册 `ui-test-20260804@example.com`(密码 `ui-test-password-123`), 昵称「UI测试用户」
- **服务状态**: 后端 ✓ / 前端 ✓ / vision 服务(18081)未启动(图片识别按设计降级提示, 见 §3)
- **修复情况**: 本报告仅记录与定位问题, **未做任何代码修改**。

---

## 1. 问题汇总(按严重度)

| # | 严重度 | 问题 | 位置 | 状态 |
|---|--------|------|------|------|
| P1 | 🔴 高 | 消息气泡的「复制 / 分享 / 删除」按钮全部无功能(死按钮) | `frontend/src/components/chat/MessageBubble.tsx:102-140` | 已复现 + 已定位 |
| P2 | 🔴 高 | 会话删除后无任何恢复入口(恢复功能是不可达死代码) | `frontend/src/pages/ChatModePage.tsx:286-308, 369-389` + `Sidebar.tsx:80-90` | 已复现 + 已定位 |
| P3 | 🟡 中 | 会话重命名后主区标题不刷新, 需手动刷新页面 | `Sidebar.tsx:281-284` + `ChatModePage.tsx:328` | 已复现 + 已定位 |
| P4 | 🟡 中 | 模型选择器显示「暂无可用模型」, 与正在使用的 env 模型矛盾 | `frontend/src/components/chat/ModelSelector.tsx:75-80, 112-115` | 已复现 + 已定位 |
| P5 | 🟢 低 | 代码运行器 Canvas 加载时 4 条 react-ace 拼写警告 | `frontend/src/components/widgets/CodeRunnerMainWidget.tsx:290-291` | 已复现 + 已定位 |
| P6 | 🟢 低 | 所有页面 2 条 React Router v7 future flag 警告 | `frontend/src/main.tsx`(路由未启用 future flags) | 已复现 |
| P7 | 🟢 低 | 已部署 AINA Project 的「删除项目」按钮禁用且无任何说明 | `frontend/src/pages/AllAppsPage.tsx:708-716` | 已复现 + 已定位 |
| P8 | 🟢 低 | 侧栏会话「更多操作」按钮默认 `pointer-events:none`, 仅 hover/focus 时可点, 触屏不可达 | `frontend/src/components/layout/Sidebar.tsx:334-347` | 已复现 + 已定位 |

---

## 2. 问题详情

### P1 🔴 消息气泡的「复制 / 分享 / 删除」按钮全部无功能

**复现路径**(Playwright):
1. 登录后进入任意对话, 发送消息等待 AI 回复;
2. 点击回复气泡右上角的「复制」按钮 → 剪贴板内容不变(先写入 `MARKER_BEFORE_CLICK` 再点击, 读回仍是 `MARKER_BEFORE_CLICK`);
3. 点击「分享」按钮 → 按钮仅获得焦点(`[active]`), 无弹窗、无 toast、无任何网络请求(`browser_network_requests` 中无 share 相关请求);
4. 点击「删除」按钮 → 消息未删除, 无任何反应。

**源码定位**: `frontend/src/components/chat/MessageBubble.tsx`

```tsx
// L102-117: AgentActions 只渲染图标按钮, 没有任何事件处理
<ActionIcon icon={<Copy />} label="复制" />
<ActionIcon icon={<Share2 />} label="分享" />
<ActionIcon icon={<Trash2 />} label="删除" tone="danger" />

// L119-140: ActionIcon 组件根本不接收 onClick
function ActionIcon({ icon, label, tone = "default" }) {
  return (
    <button type="button" aria-label={label} className={...}>
      {icon}
    </button>
  );
}
```

**影响**: 三个按钮外观可点、无禁用态, 但点击全部无效。用户无法复制回复内容、无法删除消息; 同时 P2 中主区删除/恢复路径也依赖这里, 导致删除会话只能走侧栏。

**建议(未实施)**: 为 `ActionIcon` 增加 `onClick` 并分别实现复制(写剪贴板)、分享(如无后端分享能力应先隐藏按钮或给出「暂不支持」提示)、删除(调 `DELETE /conversations/{id}/messages/...` 或至少给出明确反馈)。

---

### P2 🔴 会话删除后没有任何恢复入口(恢复功能为死代码)

**复现路径**(Playwright):
1. 侧栏「更多操作 → 删除 → 确认删除」删除**当前正在查看**的会话;
2. 页面直接跳转到 `/chat` 空状态, **没有出现**「已删除, 可恢复」界面;
3. 侧栏会话列表刷新, 该会话消失; 数据库侧为软删除(status=`deleted`), 后端 `/conversations/{id}/restore` 接口存在(`backend/.../api/conversations.py:51`), 但前端没有任何路径可以触达它。

**源码定位**:

- `frontend/src/pages/ChatModePage.tsx:286-308` 中 `deleteConversation()` / `restoreConversation()` / `DeletedConversation` 组件(L388、L644)均已实现, 但:
  - `grep "setConfirmDelete(true)" src/` → **零调用**, L369-381 的删除确认横幅是死代码;
  - 消息气泡的删除按钮是死按钮(见 P1), 主区没有任何删除入口;
- `frontend/src/components/layout/Sidebar.tsx:80-90` 侧栏删除路径:

```tsx
await api.delete(`/conversations/${conversationId}`);
setPendingDelete(null);
if (location.pathname === `/chat/${conversationId}`) navigate("/chat");  // 直接跳走, 无恢复提示
await load();
```

**影响**: 用户误删会话后**无法通过 UI 恢复**, 与产品宣称的「删除后可以立即恢复」(L372 文案)及后端能力矛盾。

**建议(未实施)**: 侧栏删除后若删除的是当前会话, 应停留并展示恢复界面(复用 `DeletedConversation`), 或删除非当前会话时给出可撤销提示条。

---

### P3 🟡 会话重命名后主区标题不刷新

**复现路径**(Playwright):
1. 侧栏「更多操作 → 重命名」, 将「你还记得关于我的什么信息吗?…」改为「记忆测试对话(重命名)」, 点确认;
2. 侧栏标题立即更新, 但主区 `<h1>` 仍显示旧标题「你还记得关于我的什么信息吗?…」;
3. 刷新页面(`page.goto` 同 URL)后, 主区标题才变为新名称。

**源码定位**: `Sidebar.tsx:281-284` 保存成功后只 `setRenaming(false)` + `notifyConversationsChanged()`(通知会话列表刷新), 但 `ChatModePage.tsx:328` 的 `title = conversation?.title` 依赖自身 `conversation` 状态, 该状态未同步更新。

**影响**: 轻微状态不同步, 影响观感; 刷新后恢复一致。

**建议(未实施)**: 重命名成功后广播事件携带新标题, 或 ChatModePage 监听会话变更后重新拉取当前会话。

---

### P4 🟡 模型选择器「暂无可用模型」与在用模型矛盾

**复现路径**(Playwright):
1. 聊天页点击「当前模型: deepseek-v4-flash」;
2. 下拉列表显示「暂无可用模型, 请先在「设置」中添加 Provider。」;
3. 但当前对话实际正在使用 `deepseek-v4-flash`(后端通过 `UNIBOT_LLM_*` 环境变量配置)。

**源码定位**: `frontend/src/components/chat/ModelSelector.tsx`

- L75-80 `visibleProviders` 只包含 `settings.providers` 中启用模型的 Provider;
- L112-115 `visibleProviders.length === 0` 时显示「暂无可用模型…」;
- `active.source === "env"` 的模型(环境变量来源)不会进入列表, 也无法被选中/切换。

**影响**: 文案误导(明明有模型在用); 用户无法在 UI 中把 env 模型选为默认(虽然它已经是默认), 也无法看到它的存在。当用户添加了 Provider 后 env 模型会被覆盖且无法切回。

**建议(未实施)**: 当 `active.source === "env"` 时, 在列表顶部展示该模型并标注「环境变量」来源(禁用切换), 同时把空列表文案改为「当前使用环境变量模型, 添加 Provider 后可切换」。

---

### P5 🟢 代码运行器 Canvas 的 react-ace 拼写警告

**复现路径**(Playwright): 打开 `/canvas/unibot-code-runner`, 控制台出现 4 条警告:

```
misspelled option "enableBasicAutocompletion"
misspelled option "enableLiveAutocompletion"
(各出现 2 次)
```

**源码定位**: `frontend/src/components/widgets/CodeRunnerMainWidget.tsx:290-291`, `AceEditor setOptions` 中传入了这两个选项。该版本 ace/react-ace 不识别这两个拼写(ace 对应选项为 `enableBasicAutocompletion`/`enableLiveAutocompletion`, 与 autocomplete 扩展加载方式有关)。

**影响**: 仅控制台噪音, 功能无影响(两个选项值均为 `false`)。

---

### P6 🟢 React Router v7 future flag 警告

**复现路径**(Playwright): 每个页面加载控制台均有 2 条警告:

```
React Router will begin wrapping state updates in React.startTransition in v7.
Relative route resolution within Splat routes is changing in v7.
```

**位置**: 前端路由初始化(`main.tsx` 中 `<BrowserRouter>`), 未启用 `v7_startTransition` / `v7_relativeSplatPath` future flags。

**影响**: 升级 React Router v7 前的兼容性提示, 当前无功能影响。

---

### P7 🟢 已部署 AINA Project 的「删除项目」按钮禁用且无说明

**复现路径**(Playwright):
1. 能力中心 → 导入模板 ZIP → 部署项目;
2. 项目卡片「删除项目」按钮变为禁用(灰), 但**没有任何 tooltip / 文字说明原因**;
3. 同一卡片中「取消部署」按钮可用。

**源码定位**: `frontend/src/pages/AllAppsPage.tsx:708-716`, `disabled={busyProjectId !== null || project.status === "deployed"}`, 无 title/提示。

**影响**: 用户无法理解为何不能删除, 只能先「取消部署」再删(两步入)。轻微可用性问题。

---

### P8 🟢 侧栏会话「更多操作」按钮触屏不可达

**复现路径**(Playwright):
1. 会话行默认状态下按钮 `pointer-events: none; opacity: 0`(JS 实测);
2. 鼠标悬停行后才变为可点击; 若鼠标移出再直接点击按钮, Playwright 报「`<a>` intercepts pointer events」;
3. 侧栏在移动端(≤某断点)被压缩为图标栏、不显示会话列表, 因此主要影响**桌面触屏设备**(无 hover 概念)。

**源码定位**: `frontend/src/components/layout/Sidebar.tsx:334-347`, class 中 `pointer-events-none opacity-0` + `group-hover:pointer-events-auto`。

**影响**: 触屏用户无法对会话执行重命名/删除; 键盘用户需 Tab 聚焦到按钮(focus-visible 有处理)。低严重度但值得注意。

---

## 3. 已验证正常的功能(回归基线)

| 功能域 | 结果 |
|--------|------|
| 注册 / 登录 / 退出 / 错误密码提示(「邮箱或密码错误。」) | ✅ |
| 未登录访问 `/chat` 重定向 `/login`, GitHub 登录链接带 `next` 参数 | ✅ |
| 流式对话回复 + 会话自动创建 + 侧栏实时更新 | ✅ |
| 多轮对话与**跨会话记忆召回**(保存「喜欢傍晚工作」后新会话成功召回) | ✅ |
| 消息反馈: 点赞 / 点踩 / 取消评价 | ✅ |
| 会话搜索过滤、重命名(侧栏)、删除、新建 | ✅(见 P2/P3 例外) |
| `open_aina` 工具: 对话中说「打开文档编辑器」→ 自动跳转 Canvas | ✅ |
| Canvas 双栏(对话+应用)、移动端「显示对话/显示应用」切换 | ✅ |
| 代码运行器: Python/Bash 执行、超时、停止容器(提示保留工作区)、停止后自动重启、执行历史 | ✅ |
| 文档编辑器: 新建/编辑/预览分栏/保存/任务模式/修改任务表单 | ✅ |
| 记忆管理 Canvas: 添加/分类统计/搜索/删除/「在对话中询问」 | ✅ |
| 图片识别 Canvas: vision 服务不可用时显示「服务不可用, 点击重试」+ 503 状态 | ✅(降级正确) |
| 能力中心: 6+1 项能力、能力详情对话框(Tool/Skill/UI/权限/Schema) | ✅ |
| AINA Project 闭环: 模板下载 ZIP → 导入校验 → 部署 → 安装授权 → 对话中远程调用成功 | ✅ |
| 设置页: env 模型展示(来自环境变量)、新增 Provider 表单 | ✅ |
| OBS 个人总览: Token 统计、模型消耗表、GitHub Calendar 热力图(含今日数据) | ✅ |
| 定时任务: 编辑器表单、仅远程 AINA 可调度(空列表提示正确) | ✅ |
| 管理后台: 普通用户访问 `/admin/*` 显示 403 权限页 | ✅ |
| 移动端 480px 布局: 侧栏折叠为图标栏, Canvas 可切换对话/应用 | ✅ |

---

## 4. 附注

- 测试期间产生的数据(用户 `ui-test-20260804@example.com`、会话 3 个、记忆 1 条、文档 `ui-test-doc.md`、AINA Project `My AINA` 已部署已安装)保留在环境中, 便于复现。
- vision 服务未启动(`:18081`), 图片识别仅验证了错误降级路径; 若需验证识别链路, 请先运行 `.\scripts\start-vision.cmd`。
- 所有复现均通过 Playwright 真实浏览器交互完成, 未修改任何源码; 如需对 P1/P2 做修复验证, 可临时给 `ActionIcon` 加 `onClick` 后再回退。
