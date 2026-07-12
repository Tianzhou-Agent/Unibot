# Unibot 前后端端到端测试用例与执行报告

## 1. 测试结论

- 执行日期：2026-07-12（Asia/Shanghai）
- 后端 Python API E2E：**6/6 通过**
- 前端 Playwright E2E：**0/4 执行，4/4 阻塞**
- 仓库既有后端回归：**57 通过，1 跳过**
- 前端生产构建：**通过**；Vite 提示单个产物超过 500 kB，不影响本次测试

前端阻塞不是用例失败。当前 Codex 浏览器安全策略明确拒绝访问
`http://127.0.0.1:5173`，并禁止切换到其他浏览器表面规避限制，因此本次不能诚实地把
Playwright 用例记为通过。用例代码、配置、依赖声明和锁文件已落库；解除本地地址限制并安装依赖后可直接复跑。

## 2. 范围与方法

### 2.1 后端

入口为 `backend/scripts/e2e_api_cases.py`。脚本通过 FastAPI `TestClient` 从 HTTP/ASGI
边界进入，贯穿 API 路由、Agent Runtime、内存仓储、SSE、审批和 Trace。模型使用确定性
`ScriptedLLM`，远程 Tool 使用 `httpx.MockTransport`，避免真实模型波动和外部副作用。

这属于应用级 API E2E，不包含反向代理、TLS、容器网络、MySQL、Redis、S3 或真实 LLM
供应商。仓库的存储集成测试仍由既有 `backend/tests/store` 覆盖。

### 2.2 前端

入口为 `frontend/e2e/unibot.spec.ts`，配置为 `frontend/playwright.config.ts`。测试从真实
React/Vite 页面进入，在浏览器网络边界拦截 `/api/**` 并返回确定性响应，验证路由、表单、
流式消息、会话状态和管理页面渲染；不依赖真实后端或真实模型。

## 3. 后端 Python 用例

### BE-E2E-001 健康检查与管理摘要

- 前置条件：全新内存仓储，系统内置 AINA 由应用生命周期初始化。
- 步骤：请求 `GET /health`；请求 `GET /admin/summary`。
- 预期：健康状态为 `ok`；业务计数为 0；至少存在 1 个内置 AINA。
- 自动化：`case_health_and_summary`。
- 本次结果：**PASS，113.3 ms**；实际初始化 2 个内置 AINA。

### BE-E2E-002 会话创建、分类、删除与恢复

- 前置条件：全新内存仓储。
- 步骤：创建会话；修改标题和分类；按分类查询；软删除；验证读取返回 404；恢复。
- 预期：修改值持久化；过滤只返回目标会话；删除后不可读；恢复后状态为 `active`。
- 自动化：`case_conversation_lifecycle`。
- 本次结果：**PASS，97.8 ms**。

### BE-E2E-003 长期记忆增删改查与统计

- 前置条件：默认用户 `anonymous`、租户 `default`。
- 步骤：新增 preference 记忆；关键字搜索；修改内容；读取分类统计；删除并再次列表查询。
- 预期：搜索命中 1 条；修改内容持久化；统计为 1；删除后总数为 0。
- 自动化：`case_memory_lifecycle`。
- 本次结果：**PASS，87.0 ms**。

### BE-E2E-004 多轮对话、SSE 与 Trace

- 前置条件：确定性模型依次返回 `first answer`、`second answer`、`streamed answer`。
- 步骤：发起第一轮对话；复用 conversation id 发起第二轮；读取第二轮 Trace；调用
  `POST /chat/stream`。
- 预期：第二轮模型上下文含首轮回答；Trace 为 completed；SSE 同时包含
  `message.delta` 和 `message.completed`。
- 自动化：`case_chat_context_stream_and_trace`。
- 本次结果：**PASS，98.2 ms**；第二轮 Trace 含 5 个事件。

### BE-E2E-005 远程 Tool 调用与 Trace

- 前置条件：注册确定性加法 Tool；Mock Runtime 对 17 + 25 返回 42。
- 步骤：注册 Tool；以指定 capability 发起对话；读取 Trace。
- 预期：Tool 收到 `{a: 17, b: 25}`；最终回答包含 42；Trace 含 `tool.completed`。
- 自动化：`case_remote_tool_and_trace`。
- 本次结果：**PASS，76.7 ms**。

### BE-E2E-006 高风险 Tool 授权门禁

- 前置条件：注册 `side_effect_level=high` 的 Tool。
- 步骤：请求模型调用 Tool；检查待审批响应；确认审批。
- 预期：确认前远程调用次数为 0；响应状态为 `approval_required`；确认后只调用 1 次并完成。
- 自动化：`case_high_risk_approval`。
- 本次结果：**PASS，80.3 ms**；确认前 0 次、确认后总计 1 次远程调用。

## 4. 前端 Playwright 用例

### FE-E2E-001 新建会话并展示流式回复

- 前置条件：无历史会话；Mock API 提供 capability 列表、会话创建和 SSE 完成事件。
- 步骤：打开 `/chat`；输入消息并发送；等待路由切换；检查用户消息和助手回复。
- 预期：路由变为 `/chat/conv-e2e-1`；两条消息均可见；回复内容为确定性文本。
- 自动化：`FE-E2E-001 新建会话并展示流式回复`。
- 本次结果：**BLOCKED（未执行）**；本地浏览器地址被环境安全策略拒绝。

### FE-E2E-002 重命名、分类、删除并恢复会话

- 前置条件：存在 1 条活动会话。
- 步骤：打开会话；重命名；切换到 work 分类；软删除；点击恢复。
- 预期：标题和分类更新；删除态出现恢复入口；恢复后回到正常会话界面。
- 自动化：`FE-E2E-002 重命名、分类、删除并恢复会话`。
- 本次结果：**BLOCKED（未执行）**；同上。

### FE-E2E-003 在能力中心注册 Tool

- 前置条件：能力列表为空。
- 步骤：打开 `/apps`；切换 Tools；打开注册编辑器；提交内置示例 JSON。
- 预期：显示注册成功提示；新 Tool 卡片出现在列表中。
- 自动化：`FE-E2E-003 在能力中心注册 Tool`。
- 本次结果：**BLOCKED（未执行）**；同上。

### FE-E2E-004 查看运行摘要并开启 Trace Debug

- 前置条件：Mock API 返回健康状态、管理摘要和 1 条 Trace。
- 步骤：打开 `/settings`；检查在线状态和摘要；开启 Debug；查看 Trace。
- 预期：显示“后端在线”；摘要数值正确；开启 Debug 后出现 `trace-e2e-1`。
- 自动化：`FE-E2E-004 查看运行摘要并开启 Trace Debug`。
- 本次结果：**BLOCKED（未执行）**；同上。

## 5. 执行命令

后端确定性 E2E：

```powershell
cd backend
uv sync --extra dev
uv run python scripts/e2e_api_cases.py --json-output .e2e-results/backend-results.json
```

后端全量回归（将临时目录放在仓库内可避免受限环境的系统临时目录权限问题）：

```powershell
cd backend
uv run pytest -q -p no:cacheprovider --basetemp .e2e-results/pytest-tmp
```

前端 Playwright：

```powershell
cd frontend
npm ci
npx playwright install chromium
npm run test:e2e
```

结果文件默认生成到 `frontend/e2e-results/results.json`，失败时的 trace 和截图位于
`frontend/e2e-results/artifacts`。这些运行产物已加入 `.gitignore`，本 Markdown 作为人工可读记录。

## 6. 已知问题与建议

1. 本次前端执行被环境策略阻塞，需要在允许访问 `127.0.0.1:5173` 的环境复跑后更新结果。
2. FastAPI/Starlette 在运行 `TestClient` 时提示未来将从 `httpx` 迁移到 `httpx2`；当前不影响结果，
   后续升级 FastAPI/Starlette 时应评估测试客户端迁移。
3. `npm` 依赖审计报告 1 个 moderate、1 个 high 风险项。本任务未运行破坏性或可能引入 breaking
   change 的 `npm audit fix --force`，建议单独评估依赖树后处理。
4. Vite 生产构建提示主 chunk 超过 500 kB。它不是功能失败，但可在性能专项中检查首屏加载和代码拆分。
