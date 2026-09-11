# Audit remediation — 10 September 2026

This records the implementation following the [9 September audit](sota-audit-2026-09-09.md). The audit is a historical snapshot; the table below describes the updated working tree. These changes address reproduced defects and make regressions measurable. They do not establish state-of-the-art agent performance.

| Audit finding | Implemented behavior | Regression evidence |
| --- | --- | --- |
| 1. Claimed email grants admin | Only provisioned immutable user IDs grant administrator access. | [Security tests](../backend/tests/test_capability_security.py): claimed email/login denied, intended account ID accepted. |
| 2. Private registry lacks ownership | Shared Tool/Skill/remote AINA registration and deletion require admin. The server binds Tool/Skill owners and tenants; discovery, prompt construction, and execution enforce visibility. Legacy private records without ownership fail closed. | Security tests cover two accounts, forged ownership, tenant visibility, private prompt/tool exclusion, and forced invocation denial. Browser coverage checks ordinary-user registry controls. |
| 3. Unrestricted connector requests | Exact approved HTTP(S) origins are checked before registration probes and at actual HTTP requests, including redirects and A2A discovery. Empty configuration denies remote capabilities. | Security tests use mocked loopback/link-local destinations, origin lookalikes, redirects, and discovered A2A interfaces; no real internal endpoint is probed. |
| 4. Forced local runner | Compose honors driver selection. Authenticated applications refuse local execution unless the operator explicitly enables trusted development use. | Security tests check both denied execution and explicit opt-in. |
| 5. Permanently running conversations | Workers renew a Redis ownership lease every 30 seconds. Expired ownership becomes an interrupted, failed conversation; old workers cannot finish or release replacement runs. Losing ownership cancels the worker. Finishing waits briefly for concurrent state reconciliation. | [Repository tests](../backend/tests/store/test_persistent_repository_observability_unit.py) cover stale OBS status, heartbeat renewal, replacement runs on the same/different repositories, cancellation, terminal OBS status, and lock contention. [Redis tests](../backend/tests/store/test_redis_store.py) check compare-and-refresh/release semantics. |
| 6. Growing requests overflow context | Every model request includes tool definitions in the estimated input budget and reserves output space. The adapter sends the matching output limit. Oversized complete transcripts/results fail explicitly without deleting original messages or silently slicing tool output. | [Context tests](../backend/tests/test_context_compression.py) cover a 60,000-character tool result and an oversized compression request; [adapter tests](../backend/tests/test_llm_client.py) check the network payload and rejection before HTTP. |
| 7. Truncated answers appear complete | Output-limit finishes preserve partial text, mark the run failed/incomplete, and never execute partial tool calls. | [Resilience tests](../backend/tests/test_agent_resilience.py) cover text and tool-call truncation, persisted state, and trace status. |
| 8. Transient failures cannot recover | A read-only Tool may be attempted up to three times by the model after explicitly retryable failures. Successful duplicates and uncertain side effects remain blocked. Transport retries also require a read-only Tool; legacy AINA invocation is not automatically replayed. | Resilience tests cover recovery, exhaustion, duplicate success, side-effect transport guards, and agreement between model-visible errors and trace events. |
| 9. Biased/lossy evaluation adapter | Graders receive observed arguments, outputs, failed attempts, repeated calls, and spans. The adapter no longer endorses every step as necessary. The addition evaluation checks exact operands and result. | [Offline evaluation controls](../backend/tests/test_eval_adapter.py) reject wrong arguments/results, missing/duplicate executions, and failures without making model calls. |
| 10. No CI quality gate | [Quality checks](../.github/workflows/quality-checks.yml) run locked backend dependencies/tests and frontend build/browser tests on PRs and before image publication. Test artifacts are retained. | Local equivalents are exercised; workflow and Compose YAML parse successfully. Comparative live-model evidence remains outstanding. |
| 11. Failed send loses text | Chat and Canvas preserve failed drafts with a retry message. Successful delivery clears the submitted draft, including when a later title update fails. Navigation keeps drafts from appearing in another conversation or AINA. | Browser tests cover creation/stream failures, retries, completed-send metadata failure, and switching during a stream. |
| 12. IME confirmation submits | Both composers check composition state, native `isComposing`, and the IME key-code fallback while preserving Enter/Shift+Enter behavior. | Browser composition and shortcut tests on Chat and Canvas. |
| 13. Sidebar blocks phone composer | Phone navigation starts collapsed and expands as an overlay with backdrop/Escape dismissal; desktop navigation remains open. | Browser tests at 390×844 verify both send buttons fit and work, then check desktop navigation. |

The earlier Copy-button fix and its clipboard success, failure, and independent-message tests remain included. Browser fixtures were also updated for the existing user-based admin observability interface and current identity menu, and now assign distinct IDs to new conversations; those tests retain their I/O, ownership, navigation, and clipboard assertions.

Final storage validation also reproduced a Windows archive failure: concurrent writes sometimes resolved an in-root path with a `\\?\` prefix, causing the containment check to reject it. NAS containment comparisons now recognize equivalent disk and UNC paths while filesystem operations retain the resolved path. [NAS regressions](../backend/tests/store/test_nas_store.py) check both alias directions, outside roots, sibling prefixes, different drives/servers, and real Windows reads/listing. A separate stress check completed 960 concurrent archive writes across six independent roots.

## Final local verification

Executed on Windows with Python 3.12.13 and Node.js 24. Commands run from the indicated package directory; backend checks use its installed `.venv` and `DEEPEVAL_TELEMETRY_OPT_OUT=true`.

| Check | Result |
| --- | --- |
| Backend: `python -m pytest -q -p no:cacheprovider --basetemp=test-temp-validated8` | **402 passed, 44 skipped**, 62.46 seconds. Skips cover opt-in real-model/storage tests and unavailable MySQL checks. One dependency deprecation warning remains. |
| Frontend: `node node_modules/playwright/cli.js test` | **58 passed**, zero failures, skips, or retries. The mobile case also passed three consecutive focused runs. |
| Frontend: `npm run build` | TypeScript and production build passed; the existing large-chunk warning remains. |
| Changed/new Python files: `python -m ruff check --no-cache …` | All checks passed. |
| Configuration and patch validation | Both workflow files and both Compose files parse; remediation links resolve; `git diff --check` passes. |

The Playwright test assertions completed normally. On this Windows host, its Vite teardown hung until the test-owned Vite process was stopped; the runner then exited successfully and wrote the report. The new Ubuntu CI workflow has not yet been executed on GitHub.

## Deployment changes

Read the [deployment migration instructions](container-deployment.md#管理员与远程能力) before upgrading:

- Replace email/login entries in `UNIBOT_ADMIN_IDENTITIES` with confirmed account IDs from `/auth/me`.
- Set `UNIBOT_CAPABILITY_ALLOWED_ORIGINS` to controlled connector origins. Internal services require an explicit entry. DNS integrity and network egress restrictions remain operator responsibilities; the origin check does not pin DNS or isolate IP addresses.
- Configure Kubernetes/gVisor and its credentials, connectivity, and volumes for untrusted code. `UNIBOT_SANDBOX_ALLOW_UNSAFE_LOCAL=true` is only for trusted development. The local driver is not an OS isolation boundary.
- An abandoned run becomes recoverable when its ownership lease expires, at most 15 minutes after the last renewal. Recovery reports interruption and never automatically replays remote actions. This is not an exactly-once guarantee for external side effects.

These guards use the effective application authentication setting. The deployed `main:app` enforces authentication; explicitly constructed no-auth development/test applications retain their test behavior.

## Evidence still needed

The deterministic tests are regression evidence, not a capability benchmark. Live MySQL/Redis fault injection, a Kubernetes/gVisor deployment check, load measurements, human IME validation, and repeated frontier-model comparisons were not run. The context estimator is approximate; large unsummarizable tool groups fail clearly instead of continuing indefinitely. Result pagination and stronger long-task compaction remain future capability work. The frontend production build still reports a large-chunk warning.

For a model comparison, use the same versioned task set, tool data, prompts, model configuration, and independent judge for each candidate; run multiple trials and retain per-trial outcome, actual tool arguments/results, token usage, latency, and failure class. Include the offline negative controls before interpreting judge scores. Current eight real-model cases are smoke evaluations, not sufficient evidence for a SOTA claim.

The real-model cases create and remove test records. Use a dedicated, loopback-only development instance, never a shared deployment. For example, start `uv run --extra dev uvicorn tianzhou_agent_platform.main:create_app --factory --host 127.0.0.1 --port 8010` from `backend`, with the intended model configured, and set `UNIBOT_EVAL_BASE_URL=http://127.0.0.1:8010` in a separate evaluation shell. See the [evaluation instructions](../README.md) for judge settings. These opt-in runs consume model quota and are excluded from ordinary CI.
