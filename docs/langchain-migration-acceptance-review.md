> **2026-09-29：** 本报告中的阻断项 A1–A8 已全部解决，当前状态见 [迁移状态](langchain-migration-status.md)。

# LangChain 迁移验收报告

日期：2026-09-24

**结论：不通过。当前实现不能按迁移完成或可发布验收。**

范围：当前工作区的未提交迁移代码，依据迁移计划中的架构边界、兼容性合同、阶段退出条件和 Definition of done。此次验收检查实际 HTTP 调用链、仓库实现和框架接入，并运行定向回归；未执行真实模型调用、生产 MySQL/Redis 恢复、完整后端或前端测试。验收没有修改业务实现。

## 1. 验证结果

| 检查 | 本次结果 |
| --- | --- |
| chat API、resilience、service boundaries、agent_runtime、agent_integration | 95 passed / 12 failed |
| capability security、widget routing、context compression、model settings、LLM、main app、trace details | 首次 37 passed / 14 failed / 5 setup errors |
| 上述 5 个临时目录权限错误使用独立可写 basetemp 重跑 | 2 passed / 3 failed；环境错误已排除 |
| 两组去重后的测试总计 | **163 cases：134 passed / 29 failed** |
| Ruff F821 定向检查 | 4 项未定义名称：Runtime 两处，perf_counter 两处 |
| 真实仓库与 ConversationService 对接探针 | append_messages 抛 AttributeError；update_approval 不存在 |
| 独立 OrderedBatchMiddleware 实例并发探针 | 相同 call ID 的第二次独立调用被误判为重复，handler 未执行 |

部分失败是旧测试仍把工具视为字典，而新 fixture 返回 StructuredTool，需要迁移测试适配层；不能将这些直接解释成权限漏洞。但重复执行、超预算调用、内置工具异常和审批行为错误均有独立证据，不能通过删除断言解决。

## 2. 阻断项

### A1 — [P1] 新服务的仓库协议没有实际实现

[conversations/service.py:76](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/conversations/service.py:76) 调用 append_messages，审批状态更新调用 update_approval；main.py 注入的 InMemoryRepository 及继承它的 PersistentRepository 没有这两个方法。新增 runner 测试使用自定义 FakeRepo，恰好实现了这些方法，因此测试通过没有验证真实装配。

直接使用 ConversationService(InMemoryRepository()) 追加一条消息，复现 AttributeError。NativeAgentRunner 接入后第一条用户消息归档就会失败。append_messages_idempotent 目前也只是转发调用，尚未证明稳定 ID 去重或原子审批转换。

重新验收：实际内存仓库和持久仓库满足消费协议；真实装配完成两轮聊天、重复归档、批准/拒绝、并发确认测试，不能仅使用 FakeRepo。

### A2 — [P1] 同一轮对话的重复执行与重试上限失效

[tool_policy.py:138](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/core/agent_runtime/middleware/tool_policy.py:138) 每次 model→tools 批次重建去重集合，生产路径没有保留旧实现跨模型迭代的成功签名和尝试预算。下一次模型输出相同参数时，仍会再次调用外部工具。

test_repeated_identical_call_is_blocked_after_one_remote_execution 实测执行 **2 次，预期 1 次**。test_transient_retries_are_bounded_and_only_for_read_only_tools 三种条件均执行 **4 次，预期分别为 2 / 3 / 1**，其中包含低副作用工具。此问题同时涉及模型主动重复请求，不能只增加自动重试中间件解决。

重新验收：在一次业务运行及其审批续跑范围内维护执行记录和总尝试预算，保留批内去重，外部调用次数满足上述回归要求。

### A3 — [P1] 内置工具路径直接抛出 NameError

[core/agent.py:843](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/core/agent.py:843) 使用 perf_counter，但文件没有导入。该分支仍由当前生产工具适配调用。

test_describe_aina_returns_real_skills_without_opening_canvas 的工具结果明确包含 name 'perf_counter' is not defined；应用发现、导航和澄清 widget 测试也失败。Ruff F821 同时确认这两处引用。

重新验收：内置工具正常返回领域结果/widget，并通过相关 widget routing 测试；清理未运行旧方法中的 Runtime 未定义引用。

### A4 — [P1] 新循环没有最终请求预算保护

[core/agent.py:1655](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/core/agent.py:1655) 直接调用 create_agent 结果；生产 middleware 列表没有最终输入预算 guard。旧 _model_node 的预算检查已不再执行，而 _prepare_context 只在进入循环前运行。

test_large_tool_result_stops_before_next_model_call_and_preserves_full_result 和 test_oversized_transcript_never_reaches_summary_or_answer_model 都到达了不应发生的模型调用，触发 scripted model 的额外调用断言。超大工具输出后继续送入模型，违反保留原文并显式失败的合同。

重新验收：每次实际模型调用前，检查最终消息、system prompt、工具定义与输出预留；超预算不调用 provider，完整原文仍被归档。

### A5 — [P1] 生产审批仍然手动结束并重建执行，没有原生 HITL 恢复

[approval_gate.py:94](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/services/agent_integration/approval_gate.py:94) 通过 jump_to=end 结束运行；[core/agent.py:1602](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/core/agent.py:1602) 在 confirm 路径遍历 pending calls 手动执行，然后启动新的 agent 调用。这不具备计划要求的持久 interrupt 与 Command(resume=...) 恢复语义，还绕过该次手动执行的原生工具包装链。

test_invalid_high_risk_arguments_are_rejected_before_requesting_approval 实测错误返回 approval_required，说明高风险参数没有在审批之前验证。

重新验收：实际 HTTP 审批路径使用内置 HITL 和稳定 interrupt 身份；进程重建后恢复原调用，非法参数不审批，拒绝不再生成回答，未知完成状态不重放副作用。

### A6 — [P1] 批次协调状态跨会话共享，等待超时后仍执行

[tool_policy.py:95](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/core/agent_runtime/middleware/tool_policy.py:95) 的全局 _batch_registry 只以 call ID 集合为键，没有运行/会话隔离。不同实例的并发探针复现了第二个调用被第一个调用的 started 集合阻止。实际 after_model 并发交错也可能覆盖相同键；按字典大小淘汰还可能移除活动批次。

[tool_policy.py:170](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/core/agent_runtime/middleware/tool_policy.py:170) 等待前序超过 30 秒后吞掉 TimeoutError 并继续调用 handler，因此慢工具会破坏顺序执行承诺。

重新验收：协调器按运行和批次隔离，生命周期明确；取消/超时终止或显式失败，不能提前执行后序工具。补充不同会话复用 call ID、慢前序工具及取消测试。

### A7 — [P1] 生产调用链没有完成服务分离，原生模型和内存迁移未完成

[api/chat.py:19](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/api/chat.py:19) 和 stream 路由仍直接调用 core.agent.AgentRuntime，未使用新 ChatService。native_agent_enabled 只配置了未被这些路由使用的 chat_service，无法承担计划中的渐进发布或回滚开关。

[main.py:229](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/main.py:229) 默认构造 OpenAICompatibleClient，随后在 [core/agent.py:326](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/core/agent.py:326) 包成 _ChatModelAdapter。因此生产仍是 native model → legacy completion port → ChatOpenAI 的包装链。_run 继续调用自定义 _prepare_context，checkpoint thread_id 仍为 trace_id，resume 归档还使用 new_native[-6:]。

这表明 create_agent 已被使用，但不能据此判定原生模型、SummarizationMiddleware、会话级 checkpoint 和稳定 ID 归档已经迁移。

重新验收：实际 HTTP 路径穿过 ChatService → integration → core/agent_runtime；默认模型直接为 BaseChatModel；使用会话级原生状态、内置摘要和稳定 ID 归档；发布开关确实选择不同执行路径。

### A8 — [P1] 备用 NativeAgentRunner 仍不能安全接替生产路径

[runner.py:237](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/services/agent_integration/runner.py:237) 保存审批时把 user_id、tenant_id 写成空字符串，ChatService 的确认所有权检查会拒绝真实用户。恢复时重新构造 ChatRequest 也没有传递原 actor；确认没有验证传入 conversation_id 与 approval 的绑定。

同文件在审批写入失败后仍返回未持久化记录，并在终态归档失败时吞掉异常。history.py 的 archive_before_compaction 没有生产调用点，runner 仅在调用完成后保存工作消息；摘要一旦删除原消息，终态归档不能补回原文。当前测试没有覆盖这些完整边界。

重新验收：真实 actor/会话/run generation/interrupt 绑定贯穿创建和恢复；关键写入失败显式失败；摘要前归档接入实际中间件顺序；补充真实 ChatService 的批准、拒绝、跨会话确认和故障恢复测试。

### A9 — [P2] 输出状态、观测和流式响应合同退化

[core/agent.py:1703](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/core/agent.py:1703) 默认 completed；OutputGuard 将空输出变成提示文字后，应用层没有可靠的失败状态字段，test_empty_model_response_marks_run_failed 返回 completed。截断结果也缺少既有 MODEL_OUTPUT_TRUNCATED 错误信息。

工具 span、发现详情、压缩事件和 trace 元数据有失败证据。生产使用 ainvoke，随后在 [core/agent.py:1816](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/core/agent.py:1816) 把整段 final_content 当单个 delta 发出，不是原生流式转发；callback handler 仅被未接入 HTTP 的 builder 使用。响应 usage 只汇总 compression token，未汇总实际回答调用。

重新验收：使用显式执行结果/错误字段映射公开状态；接入原生 callback 和 stream；模型完成前即可收到真实增量，保留正确 tool spans、token usage 和唯一终态事件。

## 3. 阶段判定

| 阶段 | 验收结论 |
| --- | --- |
| 0：依赖和组件可行性 | 当前依赖/组件测试可运行；这不证明生产组合满足合同 |
| 1A–1B：边界与原生模型 | 部分完成；目录已拆，HTTP 链、真实仓库协议、默认模型链未完成 |
| 2：摘要与归档 | 不通过：生产仍用旧压缩；新归档 hook 未接入 |
| 3：策略与重试 | 不通过：调用上限、顺序、隔离、最终输入预算存在阻断 |
| 4：HITL | 不通过：生产使用自定义 gate 和手动恢复 |
| 5：观测与流式 | 不通过：组件存在，但生产链和兼容性不足 |
| 6：完整验证与受控切换 | 不通过：29 个定向回归失败；真实存储恢复未验 |
| 7：移除冗余机制 | 未完成：旧 completion、摘要和执行协调职责仍在生产路径 |

现有 execution status 中的全部完成标记及“窄例外”描述不能作为发布依据。上述问题直接违反已确定的合同，特别是重复副作用、持久审批、消息归档和运行隔离，必须修复后重新验收。

## 4. 本次执行命令

在 backend 目录，使用现有 .venv，不修改依赖：

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_chat_api.py tests/test_agent_resilience.py tests/test_service_boundaries.py tests/agent_runtime tests/agent_integration --tb=short
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_capability_security.py tests/test_widget_routing.py tests/test_context_compression.py tests/test_model_settings.py tests/test_llm_client.py tests/test_main_app.py tests/test_trace_details.py --tb=short
~~~

5 个 setup error 使用全新 backend/.review-tmp-<uuid> 作为 --basetemp，按出错测试名称筛选重跑，结果已计入去重统计。另运行 Ruff F821 和无外部网络调用的仓库/并发探针。未运行完整后端、前端、真实模型与生产存储验证；这些后续检查不能替代先修复已复现阻断项。

## 5. 框架核对

本地锁文件现为 LangChain 1.4.2、LangChain OpenAI 1.4.0、LangGraph 1.2.12。官方文档确认内置工具错误、重试、摘要和 HITL 均有相应能力，因此当前缺口主要是组合、接入和业务合同保持，而非单纯缺少框架 API。参考 [内置中间件](https://docs.langchain.com/oss/python/langchain/middleware/built-in) 与 [原生 HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)。
