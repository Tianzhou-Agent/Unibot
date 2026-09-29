# LangChain middleware migration — execution status

Date: 2026-09-29
Plan: [langchain-middleware-migration-plan.md](langchain-middleware-migration-plan.md)
Branch: `refactor/native-agent-runtime`

The native runtime is the only agent implementation. The legacy loop, completion port, summary engine, approval gate
and compatibility re-exports are removed. This document supersedes the 2026-09-24 status and the
[acceptance report](langchain-migration-acceptance-review.md): every blocker it lists (A1–A8) is resolved below.

## Decisions

- **Working memory** is the conversation's LangGraph checkpoint (`lc-v2:<conversation_id>`). A turn submits only its
  new input; the repository stays the complete archive.
- **No legacy migration.** A conversation without a checkpoint (never run natively, or its checkpoint was lost) is
  seeded from its archive. Summaries written by the old engine to `conversation.config["context_compression"]` are
  not read. Approvals created by the old engine have no interrupt: confirming one returns 409, denying one closes it.

## Architecture

| Layer | Package | Owns |
| --- | --- | --- |
| Runtime | `core/agent_runtime` | `build_agent` (thin `create_agent` factory) and reusable middleware. Imports only LangChain/LangGraph and the standard library; policy arrives through constructor arguments. |
| Integration | `services/agent_integration` | Capability catalog, native tools, prompts, scope, approval mapping, archive conversion, model-call observation, the `AgentRunner` that composes the middleware stack. |
| Application | `services/chat.py` | `ChatService`: actor checks, run lease, trace lifecycle, model selection, turn / confirm / deny / delete. Depends on `ConversationService` and `ModelProviderService`, not on concrete repositories. |
| Features | `conversations`, `model_providers`, `observability` | Data, narrow repository protocols and services. `model_providers` also owns the token estimate and provider error knowledge injected into the runtime. |
| Storage | `store` | Concrete repositories and `MySqlCheckpointSaver` (implements `BaseCheckpointSaver`, including `aprune`). |

### Middleware stack

Composed per invocation by `AgentRunner`; first = outermost.

| Middleware | Source | Purpose | Why it is not configuration of a built-in |
| --- | --- | --- | --- |
| `TerminalDenialMiddleware` | runtime | A denied batch closes every pending call and ends without another model call. | `HumanInTheLoopMiddleware` rejects only the reviewed calls, runs the siblings and asks the model again. |
| `TranscriptHookMiddleware` | runtime | Archives new messages before summarization can replace them. | Summarization removes working-state messages; originals must be persisted first. |
| `TurnSummarizationMiddleware` | runtime, subclass of `SummarizationMiddleware` | Built-in summarization retaining complete user turns. | `keep` cannot express turns; the built-in re-raises summary failures and cannot skip an oversized summary request. |
| `CapabilityScopeMiddleware` | Unibot | Advertised tools, system prompt, UI context, forced tool, out-of-scope history projection. | Product capability scopes (AINA activation). |
| `RequestBudgetGuard` | runtime | Refuses a final request over the model's input budget; nothing is sent. | No built-in final-request budget (tools and system prompt included). |
| `ForcedToolChoiceMiddleware` | runtime | Forced tool calls are not streamed; a provider that rejects or ignores a named `tool_choice` is asked once more, and a call still missing fails the run. | Demonstrated provider incompatibility (thinking-mode providers reject it; `mimo-v2.6-flash` ignores it in about a quarter of requests). |
| `ProviderErrorMiddleware` | runtime | Provider exceptions → platform errors (injected mapping). | Product error protocol. |
| `ModelCallRecorder` | Unibot | Model spans, trace events, `/llm-calls` records, run usage. | Product trace contract; records the exact provider request. |
| `InvalidToolCallMiddleware` | runtime | Answers tool calls whose arguments are not valid JSON. | LangChain keeps them in `invalid_tool_calls`, which nothing executes or answers. |
| `OrderedBatchMiddleware` | runtime | Ordered execution of a response's calls, same-batch and run-scoped signature deduplication. | The tool node runs calls concurrently; `ToolCallLimitMiddleware` counts calls but cannot deduplicate signatures. |
| `ModelCallBudgetMiddleware` | runtime, subclass of `ModelCallLimitMiddleware` | Per-invocation model-call limit ending with the product reply. | The built-in either raises or ends with its own English notice. |
| `HumanInTheLoopMiddleware` | LangChain | Pauses a batch with a risky call; `when` skips calls with invalid arguments. | — |
| `OutputGuardMiddleware` | runtime | Empty or truncated responses fail; calls of a truncated response never run. | No built-in guard. |
| `ToolErrorMiddleware` | LangChain | Tool failures → model-visible error envelopes. | — |
| `CapabilityCallMiddleware` | Unibot | Scope and argument checks, one tool span and event set per logical call, product retry policy. | Product trace contract and capability rules. |
| `ToolRetryMiddleware` groups | LangChain | Automatic retries of side-effect-free remote tools, one group per `retries` budget. | — |

Standalone model calls use native callbacks: `StandaloneCallRecorder` records summary calls (per run) and document
worker drafts in `/llm-calls`.

## Removed

`core/agent.py` (`AgentRuntime`), `core/llm.py` (`LLMClient`, `OpenAICompatibleClient`), `core/context_compression.py`,
`core/observation_interceptors.py`, the `core.*` compatibility re-exports (`builtin_tools`, `chat`, `conversation`,
`model_settings`, `models`, `observability*`, `observation_*`, `operations_analytics`, `repository`, `telemetry`,
`trace_details`), `services/agent_integration/approval_gate.py`, `builder.py`, `tools.py`, `state.py`,
`core/agent_runtime/state.py`, `observability/callbacks.py`, `observability/events.py`, the `native_agent_enabled`
setting, the gateway's transport retry loop and the one-off `scripts/fix_shims.py` / `scripts/rewrite_agent_events.py`.
`tests/test_service_boundaries.py` fails if any of them returns.

## Acceptance blockers

| ID | Resolution |
| --- | --- |
| A1 | `ConversationService` and `ModelProviderService` are backed by protocols the real repositories implement; all flows run on `InMemoryRepository` and, live, on `PersistentRepository`. `tests/test_service_boundaries.py` compares every protocol method's parameters with both repositories. |
| A2 | `OrderedBatchMiddleware` keeps a run-scoped ledger: identical calls run again only after a retryable failure, at most three times. |
| A3 | The legacy runtime is deleted. |
| A4 | `RequestBudgetGuard` measures every final request. |
| A5 | Native HITL with `Command(resume=...)`; invalid risky arguments never prompt. |
| A6 | Batch and ledger state live on the per-invocation middleware instance; concurrent runs are isolated (tested); a timed-out predecessor blocks its successor. |
| A7 | `/chat`, `/chat/stream`, confirm, deny and delete go through `ChatService` → `AgentRunner` → `create_agent`. |
| A8 | Approvals carry the actor, trace and interrupt identity; archive failures fail the run; originals are archived before compaction. |

## Verification (2026-09-29)

| Check | Result |
| --- | --- |
| `uv run --no-sync python -m pytest -q` (backend, CI command) | 503 passed, 45 skipped (MySQL/Redis/storage suites and live-model evals skip without their environment) |
| Live storage: `tests/store` with `TZ_STORAGE_E2E=1` and `OBS_TEST_*` against `docker-compose.storage.yml` (isolated database and Redis DB) | 137 passed, 1 skipped, 3 failed. The failures (`test_observability_query.py`: personal overview, raw log ownership, feedback context) fail identically on the pre-migration commit. |
| Live agent runtime: `tests/store/test_agent_runtime_e2e.py` | Paused approval resumes after an app restart on `MySqlCheckpointSaver`; one checkpoint per thread after pruning; next turn continues; deletion removes the thread. |
| `ruff check` on changed files | Clean except 6 pre-existing unused imports in `tests/store/test_observability_phase_four.py` and `test_observability_query.py`. |
| Live-model evals (`tests/evals`), agent and DeepEval judge both `mimo-v2.6-flash`, in-memory app (`create_app()`) | 7 passed, 1 failed. `test_memory_write_recall_and_approval_lifecycle` passes its assertions but fails Tool Correctness and Step Efficiency: the model recalled twice (a query that matched nothing, then an empty one). A first run, before the forced-call guard and the judge's tool context, failed 3: the provider ignored a named `tool_choice` in 3 of 11 forced requests, and GEval, seeing no tool output, marked the real built-in apps as invented. |

## Open items

- Conversation CRUD, the approvals list and model settings go through `ConversationService` and
  `ModelProviderService`. Their protocols previously declared methods the repositories do not have
  (`delete_conversation`, `delete_model_provider`, `get_model_provider` without the actor) and optional returns
  where the repositories raise `not_found`; they now match the repositories, and the service methods built on the
  mismatch were replaced. Capabilities, feedback, memories, schedules, documents and the remaining operations
  routes still call the aggregate repository; they belong to features outside this migration.
- A paired quality comparison against the pre-migration runtime has not been run.
