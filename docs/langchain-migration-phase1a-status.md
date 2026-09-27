# Phase 1A — Extract application and feature boundaries

Date: 2026-09-24
Status: Complete. Feature packages extracted; legacy paths remain as temporary re-export shims.

## New packages

| Package | Contents | Source |
| --- | --- | --- |
| `conversations/` | `models.py` (Message/Conversation*), `schemas.py` (Chat*/Approval*), `repository.py` (Protocols), `service.py` (ConversationService) | core/conversation.py, core/chat.py |
| `model_providers/` | `models.py` (provider/runtime settings), `repository.py` (Protocol), `service.py` (ModelProviderService) | core/model_settings.py |
| `observability/` | `models.py` (Trace*/LLMCallRecord), `callbacks.py` (ObservationCallbackHandler), `service/query/stream/writer/context/logging/telemetry/trace_details/analytics` | core/observability*.py, core/observation_*.py, core/telemetry.py, core/trace_details.py, core/operations_analytics.py, core/chat.py |
| `store/memory_repository.py` | `InMemoryRepository` + resource constants | core/repository.py |
| `services/chat.py` | `ChatService` turn/confirm/deny coordinator (legacy branch until native cutover) | core/agent.py workflow surface |
| `services/platform_tools.py` | Host discovery/navigation/clarification tools | core/builtin_tools.py |
| `services/agent_integration/state.py` | ApplicationAgentState + ApplicationContext | new |
| `core/agent_runtime/` | `factory.py` (build_agent), `state.py` (RuntimeAgentState), `middleware/model_policy.py`, `middleware/tool_policy.py` | new |

## Temporary shims (removal gate: Phase 7)

Old modules re-export from new owners so 150+ existing import sites keep working:

- `core/conversation.py` → `conversations.models`
- `core/chat.py` → `conversations.schemas` + `observability.models` + `core.errors`
- `core/model_settings.py` → `model_providers.models`
- `core/repository.py` → `store.memory_repository`
- `core/builtin_tools.py` → `services.platform_tools`
- `core/observability.py` / `observability_query|stream|writer` / `observation_context|logging` / `telemetry` / `trace_details` / `operations_analytics` → `observability.*`

## Shared error schemas

`StandardError` / `ErrorEnvelope` moved into `core/errors.py` (plan §7 owner).

## Import cycle fix

`store/repository.py` now imports resource constants and `InMemoryRepository` from `store.memory_repository` (not `core.repository`), and model types from feature packages. `store/__init__.py` unchanged.

## Verification

- Package import smoke: `core.repository`, `store.memory_repository`, `conversations.service`, `services.chat`, `core.agent_runtime`, `observability.callbacks`, `main` — OK.
- `tests/test_service_boundaries.py` — 5 passed (runtime import isolation, conversations purity, factory composition, protocol injection, archive vs native message types).
- `tests/agent_runtime/test_native_feasibility.py` — 20 passed.
- Focused regressions (llm/context/model/agent/chat/main/observation/trace) — 61 passed.

## Exit criteria

- [x] Extracted services accept injected repositories (ConversationService protocol test).
- [x] API schemas/storage field names preserved (models copied verbatim; additive fields only: `ApprovalRecord.runtime_ref`, `run_generation`).
- [x] Native runtime modules have no domain/persistence dependency (import AST check).
- [x] ChatService is the route-facing coordinator with explicit temporary legacy branch.
- [x] Host tool operations live in `services/platform_tools.py`.
