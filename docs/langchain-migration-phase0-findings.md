# Phase 0 — Baseline, dependencies, native feasibility

Date: 2026-09-24
Status: Complete. Native integration demonstrated without private factory patching.

## Baseline

- Working tree before changes: `core/llm.py`, `tests/test_llm_client.py`, `frontend/src/features/model-settings/ProviderEditor.tsx` modified; migration plan untracked.
- Focused regressions before upgrade: **50 passed** (`test_llm_client`, `test_context_compression`, `test_model_settings`, `test_agent_resilience`, `test_chat_api`).

## Dependency upgrade

| Package | Before | After |
| --- | --- | --- |
| langchain | 1.3.4 | **1.4.2** |
| langchain-core | 1.5.0 | **1.6.4** |
| langgraph | 1.2.4 | **1.2.12** |
| langgraph-checkpoint | 4.1.1 | 4.1.1 |
| langgraph-prebuilt | 1.1.0 | 1.1.0 |
| langgraph-sdk | — | 0.4.2 (transitive) |
| langchain-openai | 1.4.0 | 1.4.0 |

- `pyproject.toml` now requires `langchain>=1.3.14` (ToolErrorMiddleware floor).
- `uv.lock` updated together via `uv lock` + `uv sync --extra dev --python 3.12`.
- Focused regressions after upgrade: **50 passed**.

## Native APIs confirmed (langchain 1.4.2)

| Capability | Import | Notes |
| --- | --- | --- |
| Agent factory | `langchain.agents.create_agent` | Accepts native `BaseChatModel`, tools, middleware, checkpointer, state/context schema. |
| Tool error formatting | `langchain.agents.middleware.ToolErrorMiddleware` | `on_error`/`aon_error` return content → error ToolMessage; `None` propagates. Control-flow interrupts never reach `on_error`. Arg-binding errors handled upstream by ToolNode. |
| Summarization | `SummarizationMiddleware` | `trigger`/`keep` as `("messages"\|"tokens"\|"fraction", value)`. Uses `model.with_retry()` for the summary call. Compacts in `before_model` via `RemoveMessage` + summary HumanMessage. |
| HITL | `HumanInTheLoopMiddleware` + `InterruptOnConfig` | `allowed_decisions` list required. Resume payload: `Command(resume={"decisions": [{"type": "approve"\|"reject"\|"edit"\|"respond"}, ...]})`. Mixed batches create one `HITLRequest` with N decisions. |
| Model call limit | `ModelCallLimitMiddleware` | `run_limit` / `thread_limit`, `exit_behavior="end"\|"error"`. |
| Tool retry | `ToolRetryMiddleware` | Compose **inner** relative to ToolErrorMiddleware with `on_failure="error"` so exhausted errors surface to the formatter. |
| Middleware hooks | `before/after_agent`, `before/after_model`, `wrap_model_call`, `wrap_tool_call` | Async variants `a*`. `name` is a read-only property (class name). |
| Tool runtime | `langgraph.prebuilt.ToolRuntime` | Fields: `state`, `context`, `config`, `stream_writer`, `tool_call_id`, `store`, `tools`. |
| Checkpointer | `langgraph.checkpoint.base.BaseCheckpointSaver` | Platform `MySqlCheckpointSaver` already implements this; `InMemorySaver` for tests. |
| Callbacks | `langchain_core.callbacks.AsyncCallbackHandler` | `on_chat_model_start/end/error`, `on_llm_new_token`, `on_tool_start/end/error`, `on_retry`. |
| Streaming | `agent.astream(..., stream_mode=...)` | `_stream`/`_astream` must yield `ChatGenerationChunk(message=AIMessageChunk)`. |

## Feasibility suite

`tests/agent_runtime/test_native_feasibility.py` — **20 passed** against real `create_agent` + built-in middleware + `InMemorySaver`, driven by `tests/support/fake_chat_model.ScriptedChatModel` (native `BaseChatModel`).

Covered:

1. Native tool loop, message metadata (tool_call ids, usage), multi-tool batches.
2. Middleware composition order (outer→inner nesting of before/after_model).
3. ToolErrorMiddleware formatting vs propagation of unhandled errors.
4. ToolRetryMiddleware eligible-tool retry with attempt budget.
5. SummarizationMiddleware compaction, replacement of older content, and explicit failure (no silent history loss).
6. HITL interrupt-before-execute, mixed safe/risky batch pause (no sibling executes), rejection skips side effects.
7. Saver recreation + thread resume, actor isolation across thread_ids.
8. ModelCallLimitMiddleware run budget.
9. Native callbacks (model/tool lifecycle) and streaming event emission.
10. Documented gap: parallel tool-batch order (see below).

## Narrow gaps / Phase 3+ notes

| Gap | Impact | Mitigation |
| --- | --- | --- |
| Native `ToolNode` dispatches tool batches with `asyncio.gather` | Ordered side effects / one-AINA-activation / same-batch dedup are not guaranteed | Phase 3 `awrap_tool_call` coordinator (plan §6.3). Feasibility test records concurrent completion. |
| `SummarizationMiddleware` calls `model.with_retry()` internally | Summary path already has up to 3 attempts | Align Unibot retry ceilings when composing; do not nest another summary retry. |
| HITL resume shape is `Command(resume={"decisions":[...]})` | Custom approval adapter must map public approval IDs to this shape | Phase 4 `services/agent_integration/approvals.py`. |
| Custom `state_schema` **replaces** `AgentState` unless it extends the required fields (`messages`, `jump_to`, `structured_response`) | Easy to break routing if a bare TypedDict is passed | Prefer middleware `state_schema` extensions or inherit `AgentState`. |
| `AgentMiddleware.name` is a property | Custom middleware must not assign `self.name` | Use class names or override the property. |
| Forced tool choice + streaming | Existing OpenAI-compatible quirk (named `tool_choice` rejected while streaming) | Keep documented model hook only if reproduced against native `ChatOpenAI`. |

## Default behaviors inspected

- **Summary trimming**: `trim_tokens_to_summarize=4000` by default; `None` disables. Complete AI/Tool pairs are kept together at the cutoff (`_find_safe_cutoff_point`).
- **Summary failure**: exceptions propagate after `Runnable.with_retry` exhaustion — no silent invalid-summary replacement in the observed path.
- **Tool retries**: `ToolRetryMiddleware` defaults `max_retries=2`, `on_failure="continue"`, exponential backoff + jitter. Unibot must set these explicitly.
- **Model retries**: `ModelRetryMiddleware` exists but is opt-in; current factory uses `max_retries=0` — do not introduce SDK retries accidentally.

## Exit criteria

- [x] Supported integration demonstrated without private factory patching or reimplementing the loop/summary engine.
- [x] ToolErrorMiddleware available after upgrade to 1.4.2.
- [x] Specific unresolved gap (ordered tool effects) recorded and blocks only order-sensitive flow cutover (Phase 3).
- [x] Focused regressions green before and after dependency change.
