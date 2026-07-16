# Agent Test Matrix

This matrix covers every Agent behavior currently exposed by the product. Deterministic tests own protocol and
failure invariants; DeepEval owns real-model decisions and answer quality; Playwright owns browser rendering and
navigation. A row is complete only when every applicable layer is automated.

| Area | Deterministic contract coverage | Real-model DeepEval | Browser E2E | Required invariant |
| --- | --- | --- | --- | --- |
| Ordinary chat / no false tool call | `test_chat_preserves_multi_turn_context` | `test_ordinary_chat_does_not_call_capabilities` | FE-E2E-001 | Direct questions complete without Tool or AINA calls. |
| Application discovery | `test_list_app_builtin_persists_an_interactive_widget` | `test_list_apps_agent_flow` | FE-E2E-005, FE-E2E-006 | `list_app` is selected once and returns the installed AINA widget. |
| Open AINA | `test_open_aina_builtin_returns_navigation_widget_through_agent` | `test_open_aina_agent_flow` | FE-E2E-006 | `open_aina` targets the requested ID and returns a Canvas navigation action. |
| Clarification form | `test_clarification_builtin_returns_a_host_rendered_prefilled_form` | `test_clarification_form_agent_flow` | Widget rendering covered by FE-E2E-006 registry path | Missing fields are requested without losing known values. |
| Multi-turn context | `test_chat_preserves_multi_turn_context` | `test_multi_turn_context_agent_flow` | FE-E2E-001, FE-E2E-005 | A follow-up sees prior turns and does not persist transient context as memory. |
| Memory write and recall | `test_explicit_remember_request_loads_memory_tools_and_persists_fact`, `test_explicit_recall_uses_memory_tool_and_returns_stored_fact` | `test_memory_write_recall_and_approval_lifecycle` | Memory Widget is exercised through FE-E2E-006 Canvas | Durable data is isolated by actor, persisted, recalled, and cleaned after the test. |
| Approval confirm and deny | `test_high_risk_tool_waits_for_confirmation`, `test_high_risk_tool_denial_closes_pending_call_without_execution`, `test_forget_memory_requires_approval_then_deletes` | Memory lifecycle verifies deny leaves data and confirm deletes it | FE-E2E-007 | High-risk calls never execute before approval; denial closes the pending call. |
| Remote Tool success | `test_tool_loop_executes_remote_tool_and_records_trace` | `test_remote_tool_agent_flow` | Capability registration covered by FE-E2E-003 | Arguments, actor context, result, and trace cross the HTTP boundary. |
| Remote AINA routing and Widget | `test_register_install_and_automatically_invoke_remote_aina`, routing tests in `test_widget_routing.py` | `test_remote_aina_routing_agent_flow` | Canvas rendering covered by FE-E2E-006 | Only installed and authorized AINAs route; Protocol 1.0 output and Widget persist. |
| Capability failure isolation | `test_tool_failure_is_isolated_and_returned_to_the_model`, `test_agent_resilience.py` | Not applicable: failures are injected deterministically | Debug rendering covered by FE-E2E-004 | Invalid JSON/schema/output, timeout, and invalid AINA responses become tool errors rather than crashing the loop. |
| Retry and loop bounds | `test_timeout_retries_then_returns_retryable_error_to_model`, repeated-call and iteration-limit tests | `StepEfficiencyMetric` runs where the Judge can distinguish optional steps from mandatory runtime protocol | Run summary covered by FE-E2E-004 | Transport retries are bounded, an identical successful call executes once per run, and model iterations never exceed configuration. |
| Streaming, recovery, and trace | streaming/running-state tests in `test_chat_api.py`; detailed event, AINA ownership graph, exclusion reason, and redaction tests in `test_trace_details.py` and `test_widget_routing.py` | Every DeepEval case loads and evaluates the real trace | FE-E2E-001, FE-E2E-004, FE-E2E-005 | SSE completes, refresh recovers state, and trace records sanitized input, AINA-to-capability relationships, model scope, calls, results, usage, and final status. |
| Persistent MySQL/Redis/NAS | `tests/store`, including opt-in `test_storage_e2e.py` | Real evaluations use the running persistent backend | Browser reload paths above | Repository recreation restores records; Redis and NAS enforce storage contracts. |

## Commands

```powershell
cd backend
$env:UV_PROJECT_ENVIRONMENT=".test-deps"
uv run --extra dev pytest -q

$env:UNIBOT_EVAL_BASE_URL="http://127.0.0.1:8000"
$env:PYTHONUTF8="1"
$env:DEEPEVAL_TELEMETRY_OPT_OUT="true"
uv run --extra dev deepeval test run tests/evals -v

cd ../frontend
npm run build
npm run test:e2e
```

The Docker-backed storage cases remain opt-in through their existing environment marker. DeepEval cases are also
opt-in so ordinary unit tests stay deterministic and do not consume model quota. Exact output and mandatory
protocol-step invariants use deterministic assertions; semantic completion, correctness, tool selection, and
applicable step efficiency use DeepEval metrics.
