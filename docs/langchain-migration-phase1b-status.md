# Phase 1B — Native models, messages, tools and fixtures

Date: 2026-09-24
Status: Complete for the model/message/tool foundation and one-tool native path.

## Added

| File | Responsibility |
| --- | --- |
| `model_providers/factory.py` | `create_native_chat_model` / `create_model_from_runtime` / `create_model_from_settings` → native `ChatOpenAI`, `max_retries=0` by default. |
| `model_providers/tokens.py` | Shared estimate helpers for budget guards and observability. |
| `services/agent_integration/builder.py` | `build_chat_agent` — business middleware (`ToolErrorMiddleware`) and observation callbacks bound outside the runtime. |
| `services/agent_integration/history.py` | `archive_to_native` / `native_to_archive` / `legacy_history_to_native` / `new_turn_inputs`. |
| `services/agent_integration/tools.py` | `format_tool_error` (product error formatting) + `bind_echo_tool` example. |
| `services/agent_integration/state.py` | `ApplicationAgentState` / `ApplicationContext`. |
| `core/agent_runtime/factory.py` | Thin `build_agent` around `create_agent` (Phase 1A). |
| `core/agent_runtime/state.py` | `RuntimeAgentState` (Phase 1A). |

## Composition

- `main.py` constructs `ConversationService`, `ModelProviderService`, `ChatService`.
- `ChatService` uses `_LegacyAgentAdapter` → `ObservedAgentRuntime` until native cutover.
- `api/dependencies.py` exposes `conversation_service`, `chat_service`, `model_provider_service`.
- `AgentSettings.native_agent_enabled` (default **false**) is the server-owned rollout switch (plan §10.2). Client cannot select the engine.

## Tests

`tests/agent_integration/test_native_models_and_history.py` — 10 passed:

- Native factory rejects unconfigured provider; builds ChatOpenAI with `max_retries=0`.
- Archive ↔ native conversion preserves IDs, tool_call ids, tool pairing.
- Legacy history import converts all roles; new turns submit only new input.
- `build_chat_agent` one-tool native path (echo).
- ChatService native_runner path.
- Actor-scoped `ModelRuntimeConfig` context isolation.

Plus prior suites: feasibility 20, service boundaries 5.

## Exit criteria

- [x] New path uses native messages directly (history conversion at boundaries only).
- [x] No legacy completion wrapper underneath the native one-tool path (`ChatOpenAI` / `ScriptedChatModel`).
- [x] Model/provider/one-tool contracts covered.
- [x] Runtime import-isolation checks still pass.

## Remaining (Phases 2–7)

- Phase 2: SummarizationMiddleware wiring, archive-before-compaction, legacy import durability, delete custom compression engine.
- Phase 3: ModelCallLimit / ToolRetry / ordered `awrap_tool_call` coordinator / scope policy.
- Phase 4: Native HITL + public approval adapter + supersession.
- Phase 5: `observability/callbacks.py` full coverage + `streaming.py` SSE adapter; remove observation interceptors.
- Phase 6–7: Full verification, cutover, delete legacy loop / LLMClient / shims.
