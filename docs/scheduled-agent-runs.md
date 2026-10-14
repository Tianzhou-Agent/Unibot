# 定时 Agent 与指定应用任务

在侧栏「定时任务」或定时任务 AINA 画布中，新建任务可选择两种运行方式：

- **Agent 自主运行**：无需选择或安装应用。填写自然语言任务内容，触发后走与普通对话相同的 Agent 流程，由 Agent 按需选择工具或应用。
- **指定应用运行**：选择可用的内置应用，或已安装且启用的远程/托管 AINA。内置应用通过 Agent 在对应应用范围内执行；远程/托管应用保持原来的直接调用方式，不额外依赖语言模型。

Agent 执行使用任务所属用户和租户的模型配置、权限及工具目录，因此需要先配置可用的模型。每次创建独立对话，执行历史和调试结果提供「查看运行对话」入口。远程/托管应用继续记录其协议输出，不额外创建聊天对话。

## 调度与调试

支持固定间隔（至少 10 秒）或五段 Cron，并保留时区与下次执行时间。服务需保持运行；本地和生产使用相同的后端调度器，不依赖浏览器一直打开。

「立即调试」使用已保存的运行方式和目标应用、本次编辑的自然语言输入。调试输入不会覆盖保存的任务内容，也不会改变下次计划时间。编辑任务可以在两种方式之间切换，或者更换应用。

通用 Agent 和内置应用复用普通对话的审批边界：需确认的操作记为「待确认」，不会自动批准。点击「打开运行对话并确认」继续操作。定时执行记录保存本次调度返回时的结果快照；之后在对话中审批、继续执行的最新状态以该对话为准。后续计划触发会创建新的独立运行，不复用旧审批。

多节点必须连接相同的持久数据库和 Redis。Redis 计划时间租约、任务运行租约，以及获得租约后重新检查任务配置，共同阻止节点争抢和手动/定时运行重叠。它们不是跨 Redis、数据库和外部服务的分布式事务：节点崩溃或租约失效时，不能承诺外部副作用严格 exactly-once；有副作用的应用仍应按调用 ID 做幂等处理。

## 接口兼容

沿用 `/aina-schedules` 接口与现有数据库列，不引入表结构变更：

- 创建时省略 `aina_id`，或传 `null` / `""`：Agent 自主运行。必须有非空 `prompt` 或 `input.message`。
- 非空 `aina_id`：指定应用。远程/托管应用必须安装、启用并授予所需权限；内置应用须在用户可用目录中。
- PATCH 中显式传 `aina_id: ""` 或 `null` 可切换到 Agent；省略则保持目标不变。
- 手动执行沿用 `POST /aina-schedules/{id}/run`，可临时覆盖 `prompt` / `input`；历史沿用 `GET /aina-schedules/{id}/executions`。
- 新增状态 `approval_required`。Agent 结果包含 `content`、`conversation_id`、`trace_id` 等普通聊天结果字段。

当前主干自身的数据库启动逻辑会在结构指纹不匹配时重建应用表。本功能没有修改结构指纹，但从旧版本升级主干之前仍应备份已有数据。

## 参考与取舍

参考 [Codex Automations](https://developers.openai.com/codex/app/automations) 的计划提示词与独立后台任务、[LangGraph Cron jobs](https://docs.langchain.com/langsmith/cron-jobs) 的整个 Agent 调度，以及 [Dify Schedule Trigger](https://docs.dify.ai/en/cloud/use-dify/nodes/trigger/schedule-trigger) 的应用级触发与立即测试。本次仅增加运行目标的两种选择，复用现有 APScheduler、Redis 和 Agent 流程，没有额外引入工作流平台或任务队列。
