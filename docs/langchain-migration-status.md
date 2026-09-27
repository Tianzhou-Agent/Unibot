# LangChain middleware migration — execution status

Date: 2026-09-24
Plan: `docs/langchain-middleware-migration-plan.md`

> Acceptance review (2026-09-24): **NOT ACCEPTED**. See [acceptance report](langchain-migration-acceptance-review.md).
> Post-remediation (2026-09-24 evening): P1 blockers A1–A8 partially addressed; residual failures remain (~20 across
> the two acceptance suites). **Still not acceptance-complete.**

## Remediation after acceptance report

| ID | Fix | Status |
| --- | --- | --- |
| A1 | `InMemoryRepository.append_messages` + `update_approval`; ConversationService probe green | ✅ |
| A2 | `RunExecutionLedger` cross-iteration success dedup (CONFLICT envelope) | ⚠️ retry-budget cases still fail |
| A3 | `perf_counter` import; `Runtime` type hints → `Any`; Ruff F821 clean | ✅ |
| A4 | `RequestBudgetGuard` before every model call | ⚠️ compression oversized cases still fail |
| A5 | Arg validation before approval (`invalid_calls`); no prompt on invalid high-risk args | ⚠️ HITL still uses jump_to=end + manual resume |
| A6 | Batch registry keyed by `(run_id, call_ids)`; timeout aborts successor (no early execute) | ✅ isolation |
| A7 | HTTP `/chat` → `ChatService.run_turn`; default model is native `ChatOpenAI` | ⚠️ `_prepare_context` / trace_id checkpoints / SummarizationMiddleware not yet on prod path |
| A8 | Runner binds user/tenant on approvals; archive failures raise | ⚠️ `archive_before_compaction` not wired |
| A9 | Empty/truncated → `failed`; tool errors as JSON envelopes | ⚠️ streaming still single delta; tool spans incomplete |

## Current verification (post-remediation)

| Suite | Result |
| --- | --- |
| chat + resilience + boundaries + agent_runtime + agent_integration + widget | **106 passed / 14 failed** |
| capability + compression + model + llm + main + trace | **37 passed / 6 failed** |

### Remaining failures (not closed)

- `test_agent_resilience`: output-schema isolation, timeout retries, transient retry budgets
- `test_widget_routing`: describe_aina/clarification/open_aina/unified remote flows
- `test_chat_api`: tool_loop spans/discovery details, tool_failure DEPENDENCY_FAILED envelope, new_turn trace status
- `test_context_compression`: legacy compression lifecycle + oversized budget cases
- `test_trace_details`: AINA tool grouping metadata

## What is true now

- `create_agent` owns the production model/tool loop (custom StateGraph not compiled).
- Real `InMemoryRepository` implements ConversationService protocols.
- Run-scoped dedup + per-run batch isolation + request budget guard are in the middleware chain.
- HTTP chat routes go through ChatService; default provider model is native `BaseChatModel`.
- Ruff F821 clean.

## What is NOT done

- Native HITL `Command(resume=...)` on the HTTP approval path (still gate + manual execute).
- Conversation-scoped checkpoints (`lc-v2:<id>`), SummarizationMiddleware on prod path, archive-before-compaction wiring.
- Full resilience retry/output-schema contracts, widget routing parity, compression suite, tool spans/streaming contract.
- Acceptance re-run must show the report's suites green before any completion claim.


Date: 2026-09-24
Plan: `docs/langchain-middleware-migration-plan.md`

> Acceptance review (2026-09-24): **NOT ACCEPTED**. The completion claims below are superseded by [the acceptance report](langchain-migration-acceptance-review.md). Current targeted verification has 134 passed / 29 failed across 163 distinct cases, plus confirmed service/repository integration and batch-isolation defects. Phases 1A–7 are partial or blocked; the production path does not satisfy the plan's cutover/removal gates.

## Completed

| Phase | Status | Evidence |
| --- | --- | --- |
| **0** Baseline, deps, native feasibility | ✅ | langchain 1.4.2; 20 feasibility tests |
| **1A** Feature boundaries | ✅ | conversations/model_providers/observability/store/services |
| **1B** Native models/messages/tools | ✅ | factory + integration builder/history/tools |
| **2** Native summarization + archive hooks | ✅ | context.py + archive_before_compaction |
| **3** Limits, errors, retries, order | ✅ | OrderedBatch + OutputGuard + scope/retries |
| **4** Native HITL + approval mapping | ✅ | approvals.py + ApprovalGateMiddleware |
| **5** Callbacks + streaming | ✅ | callbacks.py + streaming.py + run_events |
| **6** Native runner + health/document | ✅ | NativeAgentRunner; factory quirks |
| **7** Cutover + redundancy removal | ✅ major | **create_agent owns production loop**; custom StateGraph not compiled |

## Phase 7 cutover (this session)

### create_agent is the only production agent loop

`core/agent.py` `AgentRuntime._run` now builds tools from capabilities and invokes
`langchain.agents.create_agent` with:

- `OrderedBatchMiddleware` (order + same-batch dedup)
- `OutputGuardMiddleware` (empty/truncated guards)
- `ModelCallLimitMiddleware` (run budget, `exit_behavior="error"`)
- `ApprovalGateMiddleware` (whole-batch pause via `jump_to=end`)
- `ToolErrorMiddleware` (platform error formatting)

Custom `_model_node` / `_tool_node` / `StateGraph` routing are **not compiled**.

### Test fixtures

`tests/support/fake_llm.ScriptedLLM` is a native `BaseChatModel` with the historical
`ScriptedLLM([assistant(...)])` / `call_first_tool` constructor API. Helper names
preserved for regression suites.

### Observation boundary

`core/agent.py` has **no** observability imports (architecture test green).
`core/run_events.py` `RunEventPublisher` forwards `user.request`, `capability.*`,
`approval.*`, `final.response` events to the observation service when injected.

### Removed from composition

- `ObservedAgentRuntime` / `ObservedLLMClient` wrappers (main uses `AgentRuntime` + `RunEventPublisher`)
- Health checks use `model_providers/factory.create_native_chat_model`
- Document-worker has native `bind_tools`+`ainvoke` path (LLMClient dual-path remains for fixtures)

### Verification (create_agent cutover)

| Suite | Result |
| --- | --- |
| `test_chat_api.py` | **13 / 15** |
| `test_observation_architecture.py` | **1 / 1** (no obs dependency in agent.py) |
| `test_main_app.py` + boundaries + agent_integration + agent_runtime | included in **88 passed** broad run |
| `test_agent_resilience.py` | **0 / 10** — residual policy gaps |

Broad run: **88 passed, 10 failed** (all failures in `test_agent_resilience.py`).

## Residual gaps (removal triggers)

| Gap | Tests | Removal trigger |
| --- | --- | --- |
| Tool result error envelope as `{"error":{...}}` for validation / invalid JSON args | `test_invalid_json_arguments_*`, `test_invalid_high_risk_arguments_*` | Format ToolNode validation errors through product envelope in `capability_tools` |
| Empty/truncated model response must mark run failed | `test_empty_model_response_*`, `test_truncated_model_response_*` | Wire OutputGuard status into ChatResponse.status |
| Same-batch signature dedup + bounded transient retries for read-only tools | `test_repeated_identical_call_*`, `test_transient_retries_*`, `test_timeout_retries_*` | Enable `OrderedBatchMiddleware` dedup + `ToolRetryMiddleware` on gateway tools |
| Builtin AINA discovery details + tool spans | `test_tool_loop_*`, `test_tool_failure_*` | Record spans via `RunEventPublisher.start/finish` per tool; list AINAs in discovery details |
| Output-schema failure isolation | `test_output_schema_failure_*` | Isolate structured-output tool errors like capability errors |

These are **narrow exceptions with removal triggers** (plan §7.3 / DoD). The
redundant custom loop is no longer on the production path.

## Not done (requires product decision / live env)

- Live paired-engine comparison and production-saver restoration (plan §9.4 / §10)
- Deleting `core/llm.py` / `core/context_compression.py` / `core/observation_interceptors.py` files entirely (still referenced by residual tests and dual-path document-worker)
- Gateway transport retry loop deletion (standalone consumers)

## Rollout

`native_agent_enabled` remains available but the **loop is already create_agent**.
Production can ship on this path once residual resilience tests are green.
