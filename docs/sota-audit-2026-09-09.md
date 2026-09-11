**Unibot: blockers to a state-of-the-art product**

Implementation follow-up: [remediation and remaining evidence, 10 September 2026](sota-remediation-2026-09-10.md). Findings below describe the pre-fix snapshot.

Audit completed 9 September 2026; initial local reproductions began 8 September. Scope: the current working tree, agent runtime, authentication and capabilities, deployment configuration, evaluations, and mounted Chat/Canvas interfaces.

The most consequential obstacles are authorization, safe execution, reliable recovery, and trustworthy measurement. The repository already contains useful foundations: persistent graph checkpoints, actor checks for conversations, approval gates, observability, transport retries, context compression, and Kubernetes/gVisor support. Several failures occur at the boundaries between those mechanisms.

“State of the art” needs a defined task set and a measured comparison. This audit establishes concrete defects and evidence gaps; it does not establish a leaderboard position. P1 means address before relying on the affected deployment or workflow; P2 means address in the next product-quality pass. Conditional findings state their deployment requirements explicitly.

**1. Unverified registration can grant administrator access — P1**

When registration is open and an unclaimed email appears in `UNIBOT_ADMIN_IDENTITIES`, someone can register that address with their own password and become an administrator. Registration stores the claimed address immediately, and administrator matching trusts it. An isolated authenticated test returned 403 for an ordinary user's administrator request, then `is_admin=true` and HTTP 200 after registration using the allowlisted address.

Evidence: [registration](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/auth/service.py:52), [administrator matching](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/config.py:320), [API authorization](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/api/dependencies.py:142).

The default administrator allowlist is empty, and uniqueness prevents registering an already claimed account. Prefer explicitly provisioned administrator user IDs. Supporting email-based administration requires verified ownership of that email before privilege assignment. Verification should assert that an unverified allowlisted address cannot access global conversations, traces, or model records.

**2. Private capability registrations lack an ownership boundary — P1**

An ordinary authenticated member can list another member's private tool and delete it. The isolated test returned the private tool to a second non-administrator account and accepted its delete request with HTTP 204. The registry is globally keyed; the private visibility field has no owner/tenant enforcement on these routes. Related skill and remote AINA mutation routes also need authorization review.

Evidence: [list and delete routes](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/api/capabilities.py:32), [global registry](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/core/repository.py:1271), [tool visibility model](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/aina/tool/models.py:23).

Conversation ownership checks do work; this finding concerns capability registrations. Make shared registry mutations administrator-only, and bind private records to tenant/owner identities. Enforce those rules on discovery and execution as well as CRUD. The reproduced disclosure is registry metadata, not API credentials.

**3. AINA registration permits server-side requests to internal addresses — P1**

Registration immediately probes caller-supplied endpoint and health-check URLs without a destination policy. In an isolated test, an ordinary account's registration caused the mocked HTTP transport to receive requests for loopback and link-local addresses; a harmless JSON marker was returned in `last_health`. The registration returned HTTP 201.

Evidence: [registration probe](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/api/capabilities.py:77), [describe request](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/aina/gateway.py:91), [independent health URL](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/aina/gateway.py:107), [HTTP request](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/aina/gateway.py:401).

Actual impact depends on the backend's network access and the target response. No real internal endpoint or metadata secret was accessed. Gate connector publication, approve destinations for both URLs, validate resolved addresses, and restrict outbound networking. Approved enterprise integrations can retain explicitly authorized internal targets. This follows the application and network controls described by [OWASP's SSRF prevention guidance](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html).

**4. Compose forces the development code runner — P1 when accepting untrusted users**

Compose hardcodes `UNIBOT_SANDBOX_DRIVER: local`. Changing that variable in `.env` alone does not switch this service to Kubernetes. The local driver validates the initial working directory and starts an ordinary subprocess under the backend's OS identity. An earlier isolated execution read a disposable sentinel outside the assigned user's workspace.

Evidence: [hardcoded driver](C:/codebase/Unibot/docker-compose.yml:78), [execution endpoint](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/api/sandboxes.py:33), [working-directory check](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/sandbox/drivers.py:106), [subprocess launch](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/sandbox/drivers.py:150).

Important limits: [documentation explicitly restricts local execution to trusted development](C:/codebase/Unibot/docs/container-deployment.md:64), ports bind loopback, the backend is non-root, and the child environment is scrubbed. This is filesystem access under the backend identity, not a container-host escape or inherited-secret finding. Make driver selection effective and require an isolated runtime for untrusted deployments, with connectivity and mount checks before enabling execution.

**5. A stopped worker can leave a conversation permanently running — P1**

Reconciliation returns immediately whenever the stored trace status is `running`, without checking whether its worker or lease still exists. In a local reproduction using the real `PersistentRepository` with fake stores, a repository was recreated with a 24-hour-old run and an expired Redis lease. Reconciliation still returned `running`; a new request returned `CONFLICT`. Changing the trace status to completed correctly restored idle state as a control.

Evidence: [reconciliation](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/core/repository.py:1062), [running-state conflict](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/core/repository.py:969), [15-minute lease](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/store/repository.py:558), [background execution](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/api/chat.py:62).

Persistent checkpoints exist, but this stale-state path neither resumes the run nor makes it retryable. Add worker ownership/heartbeat checks and bounded orphan reconciliation. Resume from a durable run identity when possible, with idempotency for effects, or report interruption and release the conversation. Test worker termination at execution boundaries. [LangGraph's checkpointer documentation](https://docs.langchain.com/oss/python/langgraph/checkpointers) describes the persistence primitives available for recovery.

**6. Tool output can overflow context in the middle of a run — P1**

Compression occurs before entering the graph. Subsequent model requests send accumulated messages directly, while tool output is capped at a fixed 50,000 characters instead of the remaining token budget. A real-runtime offline test configured a 4,096-token window: approximately 60 estimated input tokens grew to 10,124 after one 40,000-character tool response, with no compression call.

Evidence: [initial compression](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/core/agent.py:1147), [subsequent model call](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/core/agent.py:233), [fixed output cap](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/core/agent.py:642).

This reproduces an oversized outgoing request, not a live provider rejection. Enforce a budget before every model call, accounting for tool definitions and output reserve. Compact or summarize tool results while retaining a way to retrieve necessary detail. Existing pre-run compression remains useful. [Anthropic's context-engineering guidance](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) discusses compaction and selective handling of accumulated tool output for extended tasks.

**7. Truncated model answers are marked complete — P2**

Any nonempty answer without tool calls is treated as completed, regardless of the provider's finish reason. An offline test supplied `finish_reason="length"` and the text `The first step is`; the runtime returned `status="completed"`.

Evidence: [completion decision](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/core/agent.py:243).

This makes partial answers look successful to users and downstream workflows. Handle length-limited output explicitly: preserve the partial response and expose its incomplete state, or perform a bounded continuation. Verify both ordinary completion and truncation, including a response containing partial structured output.

**8. Retryable tool failures cannot use the advertised model retry path — P2**

Identical calls are counted before execution and rejected after the first attempt regardless of its outcome. At the same time, TIMEOUT and RATE_LIMITED errors tell the model they are retryable. In a real-runtime offline test, a read-only tool timed out and the model retried the same arguments with a new call ID. It received `CONFLICT`; the gateway ran once.

Evidence: [duplicate-call guard](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/core/agent.py:380), [retry instruction](C:/codebase/Unibot/backend/src/tianzhou_agent_platform/core/agent.py:753).

Gateway transport retries already exist. This finding concerns model-directed recovery after that layer fails. Track outcome as well as signature and permit bounded retries for retryable failures on read-only or explicitly idempotent operations. Preserve duplicate protection for effects whose outcome is uncertain.

**9. The evaluation adapter hides information and supplies biased judgments — P1 for quality measurement**

The tool-correctness adapter replaces actual arguments with `{}`. The efficiency adapter labels every successful tool operation and model step as required, including an unrelated tool call. An offline check of the actual adapter functions showed different city arguments becoming identical grader inputs, and a tool unrelated to an explicit no-tools request being labeled required. The installed efficiency metric forwards that adapted trace to its judge.

Evidence: [discarded arguments](C:/codebase/Unibot/backend/tests/evals/support.py:162), [required model steps](C:/codebase/Unibot/backend/tests/evals/support.py:188), [required tool steps](C:/codebase/Unibot/backend/tests/evals/support.py:206).

This proves lost information and prejudged trace annotations; no paid judge run was used to claim a particular inflated score. Preserve arguments, outcomes, and useful trace details. Mark objectively mandatory approval steps separately. Add deliberately wrong arguments and unnecessary successful calls as negative controls that the grading system must reject.

**10. The checked-in release process does not enforce an agent-quality bar — P1 for release discipline**

The only checked-in workflow builds and publishes images on pushes to main; it runs no backend unit tests, browser tests, or real-model evaluations. Frontend compilation occurs in its Docker build. There are eight real-model test entry points, largely basic flows, and they are skipped unless `UNIBOT_EVAL_BASE_URL` is set. These are useful smoke tests but provide little evidence about difficult long tasks or comparative performance.

Evidence: [publish workflow](C:/codebase/Unibot/.github/workflows/container-images.yml:3), [evaluation opt-in](C:/codebase/Unibot/backend/tests/evals/support.py:17), [basic-flow cases](C:/codebase/Unibot/backend/tests/evals/test_builtin_flows.py:34), [test matrix](C:/codebase/Unibot/backend/tests/TEST_MATRIX.md:1).

External branch protections or privately operated CI were not inspected. Add deterministic required checks, then a versioned set of representative and failure-derived tasks with repeated trials, outcome assertions, success/cost/latency tracking, and a comparable baseline. Separate smoke/regression tests from capability evaluations. This is consistent with [Anthropic's January 2026 agent-evaluation guidance](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).

**11. A failed first send erases the user's draft — P1**

The composer clears text immediately after invoking the asynchronous send path. If creating the conversation fails, cleanup removes the optimistic message without restoring the draft. An isolated browser test returned HTTP 503 from conversation creation; the text disappeared from both composer and transcript, with no retry action.

Evidence: [immediate clearing](C:/codebase/Unibot/frontend/src/pages/ChatModePage.tsx:771), [conversation creation](C:/codebase/Unibot/frontend/src/pages/ChatModePage.tsx:282), [failure cleanup](C:/codebase/Unibot/frontend/src/pages/ChatModePage.tsx:369).

Retain the pending text until it is accepted, or preserve a failed message with retry. Verify initial-creation failure separately from failures after an existing conversation has already persisted the user's message.

**12. Confirming Chinese input can submit an unfinished request — P1**

Chat and Canvas submit every unshifted Enter without checking IME composition. An isolated browser event with `isComposing=true` submitted and cleared the Chinese draft. In this Chinese-language interface, candidate confirmation should not launch an agent task.

Evidence: [Chat handler](C:/codebase/Unibot/frontend/src/pages/ChatModePage.tsx:787), [Canvas handler](C:/codebase/Unibot/frontend/src/pages/CanvasModePage.tsx:609).

Guard composition in both handlers and verify candidate confirmation, ordinary Enter, and Shift+Enter. The reproduction used a synthetic composition event; physical IME behavior across supported browser/OS combinations still needs manual or device testing.

**13. The default layout clips the primary send flow on phones — P2**

The sidebar starts open at a fixed 264 pixels within an overflow-clipped application layout. An isolated browser measurement at a 390-pixel viewport left the main pane 126 pixels wide and the textarea approximately 60 pixels wide; the send button started at x=422.5, outside the viewport.

Evidence: [open-by-default state](C:/codebase/Unibot/frontend/src/components/layout/Sidebar.tsx:30), [fixed sidebar width](C:/codebase/Unibot/frontend/src/components/layout/Sidebar.tsx:144), [application layout](C:/codebase/Unibot/frontend/src/components/layout/AppShell.tsx:12).

A manual collapse control exists, but the default experience remains impaired. Use a narrow-screen drawer or collapsed default. Verify complete compose/send/recovery flows at phone widths, not only whether individual components render.

**Recommended implementation order**

| Order | Work | Acceptance evidence |
| --- | --- | --- |
| 1 | Close administrator, registry, and outbound-request authorization gaps; make safe execution deployment explicit. | Cross-account permission tests; unverified admin registration denied; disallowed connector destinations never contacted; isolated execution cannot read another workspace's sentinel. |
| 2 | Repair orphan recovery, context budgeting, truncation handling, and safe retries. | Worker-termination tests; every outgoing model request fits its configured budget; partial output stays incomplete; retryable read operations recover without duplicating effects. |
| 3 | Preserve drafts, guard IME input, and repair phone layout. | Browser failure tests plus actual supported IME checks and a complete phone-width send flow. |
| 4 | Correct graders and enforce quality gates. | Negative-control trajectories fail; deterministic checks gate releases; representative task results report repeated-run reliability, cost, and latency against a baseline. |

**Verification scope**

Security reproductions used disposable in-memory application state with authentication enabled and mocked outbound HTTP. The sandbox test read only a disposable sentinel. Agent tests used the actual runtime with fake model/gateway responses. Recovery used the actual persistent-repository implementation with fake MySQL/Redis stores and repository recreation. Evaluation-adapter checks executed the selected source functions without calling a judge. Browser tests intercepted API traffic; their 8 September results were checked against unchanged source on 9 September.

Live production configuration, actual worker termination in a deployed cluster, provider context rejection, human IME hardware behavior, load capacity, and comparative frontier-model task performance remain unmeasured. No production source fixes were made during this audit. A proposed maximum-iteration defect was checked and ruled out; ordinary conversation actor isolation also passed its control check.
