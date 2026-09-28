# LangChain middleware migration plan

Date: 2026-09-24

Status: Implemented (2026-09-29). See [the migration status](langchain-migration-status.md) for the final architecture, deviations and verification evidence.

## 1. Direction and scope

Use LangChain and LangGraph as the default implementation of agent execution, model access, messages, tools, working memory, summarization, approvals, limits, retries, streaming, and execution instrumentation. Keep Unibot code for its public API, authorization, domain operations, durable conversation archive, and observability backend.

The runtime lives in **core/agent_runtime/**. Conversation, model-provider, observability and persistence responsibilities live outside that package. Organize business services by feature, retain existing domain packages, and use **services/** only for application workflows and adapters that coordinate those domains with the runtime.

This revision replaces the earlier compatibility-wrapper design. The final runtime passes native chat models directly to create_agent. A permanent BaseChatModel → LLMClient → ChatOpenAI chain, custom summarization engine hidden inside middleware, or subclass-based agent instrumentation is not the target.

For every responsibility:

1. Use a built-in component and its documented configuration.
2. Add a small adapter for Unibot data or business policy where necessary.
3. Use custom middleware for a demonstrated policy that configuration cannot express.
4. Retain a custom mechanism only with a documented framework limitation and a regression test. Existing code alone is not justification.

The implementation scope includes the backend agent, document worker, model-health calls, and tests that currently depend on the custom model port. Preserve public HTTP/SSE schemas and durable user data. Native execution may require internal changes; these are identified explicitly below.

New providers, automatic model fallback, LLM-based tool selection, PII policy, Deep Agents, and replacement of the domain task system are outside scope. Using LangChain fully does not mean enabling unrelated features or requiring LangSmith.

## 2. Current baseline

All source paths are relative to backend/src/tianzhou_agent_platform/ unless prefixed with backend/. This baseline table describes current locations; Section 7 defines the target ownership and moves.

| Area | Current implementation | Intended replacement |
| --- | --- | --- |
| Loop | core/agent.py: custom StateGraph, _model_node, _tool_node | create_agent owns the model/tool loop. |
| Model access | core/llm.py: LLMClient, LLMResult, OpenAICompatibleClient around ChatOpenAI | Native BaseChatModel implementations and framework messages. |
| Working messages | Provider dictionaries; persist_from list slicing | Native message state/reducers and stable IDs. |
| Compression | _prepare_context and context_compression.py; summary cursor in conversation config | SummarizationMiddleware and checkpointed working history. |
| Approval | Pending approval ends invocation; confirm rebuilds state | HumanInTheLoopMiddleware and checkpointed resume. |
| Tool execution | Custom sequential dispatcher, validation, deduplication, errors | Native tools plus focused Unibot policies. |
| Retries | Gateway transport loops and bounded model-directed retries | Native retry components with explicit eligibility and attempt budgets. |
| Observation | ObservedLLMClient, ObservedAgentRepository, ObservedAgentRuntime | Native callbacks plus explicit application/domain events. |
| Streaming | Provider wrapper emits custom text deltas | Native streams translated into existing SSE events. |
| Persistence | Repository history and MySQL saver; thread ID currently equals trace ID | Repository archive plus native conversation working-state checkpoints. |

The reviewed lock resolves LangChain **1.3.4**, LangChain OpenAI **1.4.0**, and LangGraph **1.2.4**. These are baseline versions, not the intended final versions. The installed async tool node uses asyncio.gather, while Unibot currently executes tool batches sequentially. That difference needs a tested compatibility policy.

Before implementation, record the commit/worktree baseline and preserve existing changes in core/llm.py, tests/test_llm_client.py, and the frontend. Use [the test matrix](../backend/tests/TEST_MATRIX.md) and [the remediation record](sota-remediation-2026-09-10.md) to distinguish deliberate protections from implementation details.

## 3. Framework adoption decisions

These are the target choices. Phase 0 verifies the selected version and isolates any narrow exceptions.

| Responsibility | Default framework capability | Unibot integration |
| --- | --- | --- |
| Agent loop | create_agent inside core/agent_runtime | services/chat.py coordinates application lifecycle. |
| Model invocation | ChatOpenAI/BaseChatModel, ainvoke, astream | Actor-scoped configuration and verified provider quirks. |
| Execution messages | HumanMessage, AIMessage, ToolMessage, framework reducers | API/archive conversion only. |
| Tools | @tool or StructuredTool; ToolRuntime | Domain service calls and trusted execution context. |
| Tool state changes | Command and reducers | AINA scope, widgets, domain completion. |
| Prompt construction | dynamic_prompt or model middleware | Platform/AINA instructions, memory, UI context, current tasks. |
| Tool selection | wrap_model_call request overrides | Deterministic scope/authorization filtering and forced first tool. |
| Summarization | SummarizationMiddleware | Retention/budget configuration and archive-before-compaction. |
| Counting and usage | Native counting utilities/model counters and usage metadata | Tool-schema/output-reserve accounting; labeled fallback estimates. |
| Model-call budget | ModelCallLimitMiddleware | Translate exhaustion into the existing response/status protocol. |
| Approval | HumanInTheLoopMiddleware, checkpointer, Command(resume=...) | Public approval IDs, ownership, whole-batch policy, denial termination. |
| Tool errors | ToolErrorMiddleware | Existing error codes, retryability and recovery instructions. |
| Automatic tool retries | ToolRetryMiddleware | Eligible read-only tools, transient-error predicate, attempt budget. |
| Standalone retries | Runnable retry support where applicable | Same single-attempt operation and side-effect policy. |
| Execution instrumentation | AsyncCallbackHandler; usage callbacks where suitable | observability/callbacks.py forwards events to the observation service. |
| Chat streaming | Native message/update/custom streams | services/agent_integration/streaming.py translates once into existing application events. |
| Working memory | Native agent state and LangGraph checkpoints | Ownership, legacy import, archive reconciliation, deletion. |

See the official [middleware overview](https://docs.langchain.com/oss/python/langchain/middleware/overview), [built-in catalog](https://docs.langchain.com/oss/python/langchain/middleware/built-in), [tool runtime guide](https://docs.langchain.com/oss/python/langchain/tools), and [model guide](https://docs.langchain.com/oss/python/langchain/models).

### 3.1 Dependency policy

Upgrade pyproject.toml and uv.lock together to a verified compatible release set supporting the selected APIs. ToolErrorMiddleware currently requires LangChain 1.3.14 or later, so the current 1.3.4 lock is insufficient. Verify available stable releases during implementation and record exact resolved versions. Do not write a replacement merely to avoid the upgrade.

Configure retry eligibility and failure behavior explicitly; defaults can vary by version. Verify middleware ordering, native stream output, callback propagation, interrupt shapes, and saver serialization against the selected lock.

## 4. Compatibility contract and deliberate changes

### 4.1 Product requirements to preserve

| Contract | Required outcome |
| --- | --- |
| API | Existing chat, approval, conversation, error and SSE payloads remain usable. |
| Identity | Actor/workspace context comes from the server and cannot be overridden by generated tool arguments. |
| Visibility | Hidden tools and skill instructions do not leak; registration alone does not grant execution permission. |
| Archive | Preserve original messages, complete tool results, call IDs, widgets and historical conversation records. |
| Transient context | UI/task/memory projections do not become user messages or durable memories. Avoid checkpointing transient UI text or credentials. |
| Tool pairing | Every proposed call has a result/error/denial before closing a completed/failed turn; pending approval is the intentional exception. |
| Side effects | No execution before required approval; no automatic retry of side effects or uncertain external completion. |
| Batches | Preserve ordering required by current semantics, same-batch deduplication and the one-AINA-activation rule. |
| Context limits | Budget the final prompt and tool definitions, reserve output space, and never silently slice oversized results. |
| Output guards | Empty/truncated responses fail; partial tool calls never execute; partial user-visible text is preserved. |
| Approval | Invalid risky calls do not trigger approval; a valid risky call pauses the whole batch; denial closes pending calls without another model answer. |
| Run lifecycle | Preserve leases, renewal, stale-worker protection, worker cancellation and existing background behavior after SSE disconnect. |
| Domain behavior | Preserve routing, widgets, clarification persistence and document-task direct completion. |
| Observation | Preserve correlations, redaction, usage provenance, failure isolation and operational APIs. |

### 4.2 Explicit design changes

These changes need focused tests and release notes. Preserve observable contracts without preserving obsolete implementation assertions.

| Change | Target behavior | Compatibility handling |
| --- | --- | --- |
| Working memory | Stable conversation-scoped checkpoints carry messages/summaries between turns. | Import legacy history once; repository remains the full archive. |
| Summary lifecycle | Built-in summarization can run before model calls during a long tool loop. | Compact eligible complete history; fail when retained/indivisible content still cannot fit. |
| Summary representation | Framework-managed working state replaces continuing writes to COMPRESSION_CONFIG_KEY. | Read old summaries during import; keep old archive records. |
| Approval resume | Continue the checkpointed graph rather than reconstructing a pending tool batch. | Public approval IDs map to native runtime/thread/interrupt metadata. |
| Counters | Native execution counters survive an approval pause. | Test reset behavior; define the configured limit scope and derive public per-invocation reporting deltas where required. |
| Retry ownership | Native retry components replace custom loops at migrated call sites. | Preserve allowed physical attempts, call IDs, and side-effect restrictions. |
| Test fixtures | Scripted models implement the native chat-model contract. | Retain behavioral coverage instead of retaining LLMClient for tests. |

An indivisible oversized recent result must still fail explicitly. Native summarization is not permission to discard original data or rewrite tests so that unsafe behavior passes.

## 5. Target architecture and service boundaries

Use feature-owned packages rather than putting every service in core or placing all existing domains under a new generic services directory. Existing auth/, tasks/, aina/, sandbox/ and vision/ already provide useful feature boundaries.

- **core/agent_runtime/** owns framework execution and reusable execution policies.
- **services/chat.py** owns the application workflow that coordinates an agent turn or approval continuation.
- **services/agent_integration/** adapts conversation, AINA and other domain services to native LangChain inputs, tools and middleware.
- **conversations/** owns conversation/approval data, archive operations and run ownership.
- **model_providers/** owns provider configuration, actor-scoped model selection and native model construction, including standalone consumers.
- **observability/** owns trace data, callbacks, delivery and operational queries.
- **store/** owns concrete repositories, checkpoint saving and storage clients.

~~~mermaid
flowchart TD
    API[api routes] --> Chat[services.chat / ChatService]
    API --> Domains[Feature services]
    Chat --> Conversations[conversations.service]
    Chat --> Integration[services.agent_integration]
    Integration --> Runtime[core.agent_runtime / create_agent]
    Integration --> Providers[model_providers]
    Integration --> Obs[observability]
    Integration --> AINA[aina / tasks / other domain services]
    Runtime --> Native[Native models, tools, middleware, streams]
    Store[store adapters] -. implement .-> Ports[Feature repository contracts]
    Conversations --> Ports
    Domains --> Ports
    Main[main.py composition root] -. inject .-> Chat
    Main -. inject .-> Store
    Main -. inject .-> Runtime
~~~

### 5.1 Runtime boundary

The runtime receives native chat models, tools, middleware, callbacks, a checkpointer, state/context schemas and invocation configuration. It returns native compiled-agent behavior, messages, state updates and interrupts. It does not accept ChatRequest, load Conversation records, construct ChatResponse, write ApprovalRecord, acquire leases, resolve provider credentials, or emit HTTP SSE.

Keep this boundary aligned with LangChain's existing interfaces. Do not introduce a second Agent/Model/Tool framework, a universal plugin registry, or a custom LLM port. A thin factory returning the compiled agent is sufficient; add a runtime class only if it owns real execution behavior beyond forwarding ainvoke/astream.

Reusable guards inside the runtime can inspect native tool metadata, finish reasons, budgets and call signatures. They must not import AINA records, document services, conversation schemas, concrete repositories or the observability service. Business-specific middleware is built outside the package and passed in.

### 5.2 Application workflow and domain services

ChatService provides the existing chat/confirm/deny workflow: resolve actor-owned conversation state, obtain a run lease, prepare execution, invoke/resume the native agent, reconcile results, finish the run and return application response schemas. It translates framework outcomes into product status.

ConversationService owns conversation/approval persistence operations, idempotent archive append, run-generation/lease operations and ownership checks. It does not invoke the agent or import ChatService. Native interrupt identities are stored as opaque execution references alongside approval metadata, without embedding LangChain objects in domain models.

The integration package binds domain tools, prepares prompts and scope state, converts archive/native messages, configures native HITL, captures transcript updates and translates streams. It is the intentional place that knows both Unibot and LangChain. It contains no custom model/tool loop or summary engine.

ModelProviderService and its factory return native model instances for ChatService and document/health consumers alike. Observability callbacks live with their observation service and are injected as native callbacks; the runtime never constructs them.

### 5.3 Persistence boundary

Move InMemoryRepository out of core/repository.py to store/memory_repository.py. Keep PersistentRepository in store/repository.py and MySqlCheckpointSaver in store/checkpoint.py. Initially preserve their behavior and inheritance; splitting the aggregate repository into many implementations is a separate change unless a concrete migration requirement demands it.

Define narrow repository protocols beside the feature that consumes them, for example conversations/repository.py. Protocols import that feature's data models, not its service. The aggregate in-memory/persistent repository can implement several protocols structurally and is injected by main.py.

Do not define a giant mirrored repository interface or one abstraction per entity. Add only the contracts consumed by the extracted services. New conversation/model-provider/observation services must not type their dependencies as InMemoryRepository or PersistentRepository.

Checkpointing remains behind LangGraph's BaseCheckpointSaver contract. The runtime depends on that framework interface; store/checkpoint.py implements it. Avoid making conversation tables into graph state or putting graph message reducers in repository code.

## 6. Implementation design

### 6.1 Native models; remove the custom completion protocol

Pass ChatOpenAI or an already-supported BaseChatModel directly to create_agent. Move native construction from core/llm.py to model_providers/factory.py, provider configuration models from core/model_settings.py to model_providers/models.py, and actor-scoped selection/configuration operations to model_providers/service.py. Delete the custom completion protocol and retire core/llm.py after imports migrate.

Migrate the document worker from LLMClient.complete to native ainvoke and native response metadata. Update model-health calls and test injection to use the same model factory. These consumers are part of this migration, so they cannot indefinitely justify the old wrapper.

Keep proven provider compatibility through documented settings first. If forced tool choice is rejected or incompatible with streaming, retain a narrow model hook for that demonstrated case. Preserve separate request records for fallback requests and avoid duplicate text emissions. Do not build a generic provider abstraction around hypothetical differences.

Use native messages throughout execution. Convert only at API/archive boundaries. Preserve structured content, invalid tool-call information, call IDs, finish reasons and usage metadata. Set lower-level retries explicitly: the current model factory uses max_retries=0, so native integration must not accidentally introduce SDK retries.

ModelRetryMiddleware is available if model retries are an existing/explicitly selected policy. Adding a new retry policy is not required merely to populate the middleware list.

### 6.2 Native tools and focused policy

Use @tool for fixed tools and StructuredTool or supported schema-based construction for dynamic registry capabilities. ToolRuntime supplies trusted actor context, services and call identifiers. Use Command/state reducers for scope updates and widgets.

Use native argument validation where it matches the existing schema contract. Retain the domain JSON Schema validator only where dynamic schemas need behavior not provided by the chosen tool interface. Validation before approval remains necessary even if execution also validates inputs.

Construct the authorized registry for the invocation, filter advertised tools through model middleware, and check execution-time scope. Re-resolve permissions on approval resume. Scope activation affects the following model request; sibling calls remain governed by the original batch scope.

Configure ToolErrorMiddleware with a formatter supplied by services/agent_integration/tools.py. Runtime guards use framework-level outcomes; ChatService and the domain adapters translate platform errors. Verify malformed arguments, unknown tools and schema errors reach the supported interception boundary. Framework interrupt control flow must not be converted into a normal tool error.

Keep signature-based deduplication as a configurable execution policy in core/agent_runtime/middleware/tool_policy.py. ToolCallLimitMiddleware counts calls; it does not express “this normalized tool/argument combination already succeeded.” AINA scope discovery and permission checks belong to the application/domain integration layer.

### 6.3 Preserve ordered effects through public hooks

Inspect the upgraded framework for a supported ordering mechanism. A prompt, parallel_tool_calls=False, or max_concurrency=1 is not proof that arbitrary provider/restored multi-call messages execute in order.

If necessary, add a narrow invocation-local coordinator through awrap_tool_call, keyed by original call ID and position. The native tool node still owns dispatch, tool results and graph transitions. The coordinator only preserves the required ordering and batch-local policy.

Verify same-batch duplicate checks, one scope activation, widget order, sibling state merging and release on failure/cancellation. Do not persist asyncio synchronization primitives or hold them on shared middleware instances. Place logical-call deduplication outside native retries so retry attempts are not mistaken for duplicate model requests.

If a supported implementation needs private factory patching or a second scheduler, document the exact gap and block affected-flow cutover. Do not substitute the old custom loop and label the migration complete. Relaxing existing batch semantics requires an explicit product decision.

### 6.4 Built-in summarization and the full transcript archive

Use SummarizationMiddleware as the compression engine. Configure its prompt, trigger, retention, selected model and supported counter. Prefer framework/model counting; preserve a tested estimate only where compatible providers cannot supply a reliable count. A final-request guard separately includes system instructions, tool schemas and output reserve.

Keep the full repository archive separate from compacted working state. Archive original outputs before summarization can remove them. Preserving originals does not require a custom summary algorithm.

A thin adapter in services/agent_integration/context.py may constrain retention to complete turns, skip an oversized summary request, or retain previous state after summary failure. It delegates generation and message replacement to the built-in component. Do not copy the summarizer into a local class.

Inspect the selected version's default trimming and failure behavior. Disable/adapt silent trimming of unsummarized content and invalid-summary replacement. Test complete tool groups, empty/truncated summaries, and summaries that do not reduce the request. Record any policy that public APIs cannot express before implementing an extension.

The transcript hook in services/agent_integration/history.py calls ConversationService to persist original model outputs before approval interruption and tool outputs before the next compaction. ChatService reconciles terminal/failed/paused state at the application boundary. Use stable IDs and idempotent appends; delete persist_from slicing. Archive writes are correctness-critical, so they do not belong in callbacks that intentionally swallow observation failures.

Delete core/context_compression.py after migrating consumers and legacy import. Keep request-budget guards in core/agent_runtime/middleware/model_policy.py. If runtime and observability both need a counting helper, put the smallest shared function in model_providers/tokens.py and inject it into runtime configuration rather than making observability import runtime internals. Update the moved observability/query.py and other consumers accordingly. A legacy summary reader is temporary import code, not a second memory engine.

### 6.5 Native working memory and legacy import

Use a stable server-derived thread identifier such as lc-v2:<conversation_id>, with actor ownership verified before invocation/resume. Keep trace IDs separate. Submit only new input after initialization, rather than appending the entire repository transcript every turn.

Keep reusable execution state in core/agent_runtime/state.py and extend it in services/agent_integration/state.py for active AINA scope and widget fields. Reuse native AgentState, message reducers and built-in counters; do not make the runtime base state depend on conversation or AINA models. Invocation context holds services, credentials, current UI context and model configuration; these are not serialized.

On first native invocation of a legacy conversation, import active history using its valid summary boundary or reconstruct from the archive. Mark initialization durably and make it idempotent. Do not load old graph checkpoints into the new topology.

A new user turn with pending approval must invalidate the approval and resolve/cancel paused native work before accepting the new input. Bind approvals to a run generation so late confirmations cannot continue superseded work. Simply injecting a new user message into an unresolved interrupt is insufficient.

Missing state may be rebuilt only for quiescent conversations with no pending or uncertain execution. Missing approval checkpoints fail explicitly and never replay side effects. Propagate conversation archive/delete lifecycle to checkpoint retention/deletion under existing ownership rules.

Retain MySqlCheckpointSaver if no supported replacement satisfies storage requirements. Implementing the LangGraph saver interface is legitimate storage integration. [Native short-term memory](https://docs.langchain.com/oss/python/langchain/short-term-memory)

### 6.6 Native HITL with a public approval adapter

HumanInTheLoopMiddleware owns interruption/resumption. services/agent_integration/approvals.py maps native decisions/interrupts to application execution references and configures batch/terminal policy. ConversationService owns approval records and atomic status transitions; ChatService coordinates actor checks, leases and the actual resume. The runtime does not import approval persistence models.

Validate risky calls before approval. Verify a mixed safe/risky batch pauses before any sibling executes. Invalid risky arguments must produce errors, not approval prompts or accidental authorization.

Durably associate approval ID with runtime version, thread ID, interrupt identity, run generation and pending call IDs. Keep public serialization unchanged. Reconcile approval creation idempotently after a durable interrupt; avoid unguarded writes before interrupt code that may be re-entered.

Confirm maps to native decisions in the required order, with original arguments/call IDs and revalidated permissions. Deny maps to rejection plus a small terminal policy closing all pending calls without another generation. This adapts HITL; it must not become a replacement approval state machine.

Isolate any unsupported product requirement in a documented extension while retaining native interrupt machinery. Worker loss after an external action creates uncertain completion and must never trigger a replay fallback. [HITL guide](https://docs.langchain.com/oss/python/langchain/human-in-the-loop) and [interrupt rules](https://docs.langchain.com/oss/python/langgraph/interrupts)

### 6.7 Native retry and limit ownership

Use ModelCallLimitMiddleware with a public status/result adapter. Verify its counter lifetime across new invocations and interrupt resumes. Define the configured budget scope explicitly and derive public reporting deltas if needed. A pause must not accidentally grant unlimited additional calls.

Use ToolRetryMiddleware for explicitly eligible read-only tools, with transient-error predicates, configured attempts and stable idempotency keys. Group tools by retry configuration if needed. Configure exhausted retries to reach ToolErrorMiddleware. Exclude side-effecting tools and uncertain AINA operations.

At migrated agent call sites, the gateway performs one attempt. Replace the custom retry loop with the native owner. Migrate standalone consumers to native runnable retry support where appropriate before deleting shared gateway retry code. Preserve the existing total attempt ceiling, including subsequent model-directed attempts.

Keep the distinction between automatic attempts and model-directed repeat calls. Signature deduplication remains a product policy; new tool quotas, cross-provider fallback and changed retry eligibility are outside scope.

### 6.8 Native callbacks and streaming

Implement one AsyncCallbackHandler in observability/callbacks.py forwarding standard model/tool start, end, error and token metadata to observability/service.py. Attach it through runnable configuration for agent, summarizer, document-worker and health calls. Verify propagation and correlation rather than preserving separate wrappers. [Callback reference](https://reference.langchain.com/python/langchain-core/callbacks/base/AsyncCallbackHandler)

Native usage metadata is authoritative when present; estimates remain labeled. Distinguish logical executions from physical provider attempts. Callbacks may not expose exact HTTP bodies or SDK-internal attempts, so retain narrow transport instrumentation only for required fields that callbacks cannot provide.

Use native streaming and translate it once in services/agent_integration/streaming.py. The runtime emits native stream data; the API layer formats the SSE envelope. Filter internal generations through explicit run metadata, preserve the SSE envelope, and emit one terminal result/error. Remove the provider-wrapper token emitter after the native adapter is active. [Streaming guide](https://docs.langchain.com/oss/python/langchain/streaming)

Keep application root spans, approval/repository events and run cleanup at explicit application boundaries. No ObservedAgentRuntime inheritance is needed. after_agent alone is insufficient for exceptions, cancellation or process loss.

Move core/observability.py to observability/service.py, retaining its storage/delivery/redaction behavior. Move its related query, stream, writer, telemetry and trace helpers into that feature package. Native callbacks supply execution events to it. Moving its OTel/Redis records and operational dashboards to LangSmith would be a separate product/storage migration.

## 7. Final file structure

Use feature packages for data and business services, an application services package for coordination, and core/agent_runtime for reusable execution. This avoids a single services directory containing unrelated domain models and repositories. Keep existing feature packages in place.

The tree is the migration target, not a requirement to create empty files. [new] introduces necessary integration; [moved] relocates existing ownership; [adapted] changes behavior/imports; [conditional] is created only if shared consumers require it. Each package has a minimal __init__.py; omit broad re-export barrels.

~~~text
backend/
|-- pyproject.toml                              [adapted] Native middleware dependencies
|-- uv.lock                                     [adapted] Verified compatible release set
|-- src/tianzhou_agent_platform/
|   |-- main.py                                 [adapted] Composition and lifecycle; inject concrete adapters
|   |-- core/
|   |   |-- base.py                             [existing] Shared value types and time helpers
|   |   |-- errors.py                           [adapted] Shared errors and public error value schemas
|   |   |-- schema.py                           [existing] Shared schema utilities
|   |   |-- agent_runtime/
|   |   |   |-- factory.py                      [new] create_agent; native execution configuration
|   |   |   |-- state.py                        [new] Reusable AgentState/context fields only
|   |   |   +-- middleware/
|   |   |       |-- model_policy.py             [new] Final request budget and output guards
|   |   |       +-- tool_policy.py              [new] Signature deduplication and ordered batch policy
|   |   +-- ...                                Unrelated existing shared modules, outside runtime
|   |-- services/
|   |   |-- chat.py                             [extracted] ChatService: turn/resume orchestration
|   |   |-- platform_tools.py                   [moved] Host discovery/navigation/clarification operations
|   |   +-- agent_integration/
|   |       |-- builder.py                      [new] Bind domain adapters and pass native inputs to factory
|   |       |-- state.py                        [new] Application scope/widget state and invocation context
|   |       |-- context.py                      [new] AINA prompts, scope, memory/task projection, summary policy
|   |       |-- tools.py                        [new] Native tool schemas/adapters and product error formatting
|   |       |-- history.py                      [new] Native/archive conversion, import and transcript hooks
|   |       |-- approvals.py                    [new] Native HITL configuration and public approval mapping
|   |       +-- streaming.py                    [new] Native streams to application events
|   |-- conversations/
|   |   |-- models.py                           [moved] Conversation, Message and ApprovalRecord
|   |   |-- schemas.py                          [moved] ChatRequest/Response, ApprovalAction, CRUD schemas
|   |   |-- repository.py                       [new] Narrow persistence Protocol(s) for this feature
|   |   +-- service.py                          [extracted] Archive, approval records, ownership and leases
|   |-- model_providers/
|   |   |-- models.py                           [moved] Provider/model settings data
|   |   |-- repository.py                       [new] Settings persistence contract consumed by service
|   |   |-- service.py                          [extracted] Actor settings, defaults and model selection
|   |   |-- factory.py                          [extracted] Native model construction and proven compatibility
|   |   +-- tokens.py                           [conditional] Shared native counting/estimate helper
|   |-- observability/
|   |   |-- models.py                           [moved] TraceEvent/Span/Record and LLMCallRecord
|   |   |-- repository.py                       [new] Persistence contracts consumed by observation services
|   |   |-- service.py                          [moved] Redaction, records, delivery and observation operations
|   |   |-- callbacks.py                        [new] Native callback adapter to observation service
|   |   |-- query.py                            [moved] Operational queries
|   |   |-- stream.py                           [moved] Observation stream service
|   |   |-- writer.py                           [moved] Observation writer
|   |   |-- context.py                          [moved] Observation correlation context
|   |   |-- logging.py                          [moved] Observation logging
|   |   |-- telemetry.py                        [moved] OTel setup/helpers
|   |   |-- trace_details.py                    [moved] Trace detail projection
|   |   +-- analytics.py                        [moved] Operations analytics
|   |-- store/
|   |   |-- memory_repository.py                [moved] Concrete InMemoryRepository
|   |   |-- repository.py                       [adapted] Concrete PersistentRepository
|   |   |-- checkpoint.py                       [existing] LangGraph-compatible MySQL saver
|   |   +-- ...                                Existing database, Redis, NAS and observation storage
|   |-- api/
|   |   |-- dependencies.py                     [adapted] Resolve ChatService and feature services
|   |   |-- chat.py                             [adapted] HTTP routes and SSE envelope encoding
|   |   |-- conversations.py                    [adapted] ConversationService entry points
|   |   |-- model_settings.py                   [adapted] Provider service and native health invocation
|   |   +-- ...                                Existing routes
|   |-- aina/                                  Existing domain services and protocols
|   |   |-- gateway.py                          [adapted] Single-attempt migrated operations
|   |   +-- document/task_service.py            [adapted] Shared native model factory/invocation
|   |-- auth/                                  Existing identity/authorization
|   |-- tasks/                                 Existing domain task service
|   |-- sandbox/                               Existing sandbox integration
|   +-- vision/                                Existing vision integration
+-- tests/
    |-- agent_runtime/
    |   |-- test_composition.py                 [new] Native middleware/guards with scripted models
    |   +-- test_tool_batches.py                [new] Order, deduplication, cancellation, attempt bounds
    |-- agent_integration/
    |   |-- test_history.py                     [new] Conversion, import, compaction and archive protection
    |   |-- test_approvals.py                    [new] Interrupt mapping, resume and supersession
    |   +-- test_streaming.py                    [new] Public event mapping and generation filtering
    |-- test_service_boundaries.py              [new] Imports, dependency direction and composition smoke test
    |-- test_conversation_service.py            [new] Archive idempotency, ownership and approval transitions
    |-- test_langchain_callbacks.py             [new] Correlation, usage and standalone model calls
    |-- test_chat_api.py                        [adapted] Existing public lifecycle/SSE regressions
    |-- test_llm_client.py                      [adapted] Port cases to native model construction
    |-- test_context_compression.py             [adapted] Port cases to native summary integration
    |-- test_observation_architecture.py         [adapted] Native callbacks and explicit domain events
    |-- support/fake_llm.py                     [adapted] Scripted native chat model
    +-- store/test_graph_checkpoint.py          [adapted] Working state, restore and version isolation
~~~

Tests cover behavior and boundaries; do not add a matching test module for every file. Existing regression filenames can remain while their fixtures/assertions migrate.

### 7.1 Source-to-target moves and remaining responsibilities

| Current source | Target | Decision and reason |
| --- | --- | --- |
| core/agent.py | core/agent_runtime/, services/chat.py and services/agent_integration/ | Split execution, lifecycle coordination and business integration; delete the old loop after cutover. |
| core/context_compression.py | Built-in SummarizationMiddleware; small policy in runtime model_policy.py and integration context.py | Delete the custom engine. Optional shared counting goes in model_providers/tokens.py; no duplicate summary implementation. |
| core/llm.py | model_providers/factory.py and service.py | Native construction and actor configuration remain application concerns. Delete LLMClient, LLMResult and OpenAICompatibleClient. |
| core/chat.py | conversations/schemas.py, conversations/models.py, observability/models.py, core/errors.py | Split public chat schemas, approval data, traces and shared error schemas by owner. None belong to the execution runtime. |
| core/conversation.py | conversations/models.py and schemas.py | Metadata and full archive records remain product data. Native messages replace only execution representation. |
| core/model_settings.py | model_providers/models.py | User/provider configuration remains necessary for native model selection. |
| core/repository.py | store/memory_repository.py | It is a concrete aggregate repository, not a runtime contract. Keep behavior; inject via feature protocols for extracted services. |
| core/builtin_tools.py | services/platform_tools.py; native bindings in services/agent_integration/tools.py | Existing host workflows coordinate domains. Keep task operations in tasks and AINA protocols in aina. Replace concrete repository annotations in extracted operations with narrow consumed contracts. |
| core/observation_interceptors.py | observability/callbacks.py and explicit service/repository event boundaries | Delete proxy/subclass instrumentation after equivalent event coverage. |
| core/observability.py | observability/service.py | Keep storage, redaction and delivery, fed by native callbacks. |
| core/observability_query.py, observability_stream.py, observability_writer.py | observability/query.py, stream.py, writer.py | Move the observation feature together; update imports and dependency injection. |
| core/observation_context.py, observation_logging.py, telemetry.py, trace_details.py, operations_analytics.py | observability/context.py, logging.py, telemetry.py, trace_details.py, analytics.py | Keep correlation, diagnostics and operational APIs outside execution. |
| core/models.py | Direct imports from owning feature; temporary compatibility re-exports only where required | This file is already a compatibility barrel. Update migrated exports during transition and remove obsolete aliases after callers migrate. |

core/workspace.py and core/feedback.py are outside agent_runtime and need no behavior change for this migration. Relocating unrelated features or splitting every existing repository consumer is separate work. Existing domain imports affected by the repository/model moves must still be updated.

LangChain supplies execution mechanisms; it does not replace Unibot's conversation ownership, provider settings, public payloads, storage deployment or observability retention. Those responsibilities justify the external feature packages. Retained files must not hide a second framework implementation.

### 7.2 Dependency and ownership rules

| Package | Allowed role/dependencies | Exclusions |
| --- | --- | --- |
| core/agent_runtime | Native LangChain/LangGraph APIs, standard library, local runtime modules and genuinely shared core helpers | services, conversations, model_providers, observability, aina, store, api; no public DTOs or repository calls |
| services/chat.py | Feature services, integration adapters and native invocation/resume interfaces | Concrete repositories, HTTP transport and a custom model/tool loop |
| services/agent_integration | Runtime, native APIs and domain/service contracts | Concrete store implementations; domain operations duplicated inside tool wrappers |
| conversations | Its models/protocols, shared values and authorization contracts | ChatService, agent runtime, LangChain message/checkpoint objects and concrete store imports |
| model_providers | Settings models/protocols, native model/provider APIs and shared helpers | Agent runtime and chat workflow |
| observability | Its data/contracts, native callbacks and storage delivery interfaces | Agent runtime internals; correctness-critical transcript writes |
| store | Feature models/protocols, storage clients, framework saver interface | Application orchestration services or API routes |
| api | Application/feature services and public schemas | Direct agent construction or new concrete repository access in migrated routes |
| main.py | Concrete implementations and all composition inputs | Business execution logic |

The framework agent factory composes native execution; the application builder chooses business policies and dependencies. These two functions serve different ownership boundaries and need no additional factory classes. Keep builders free of per-turn shared mutable state.

Native state/context fields shared by execution policies live in runtime/state.py; AINA/widget extensions live in integration/state.py. Built-ins are imported from LangChain, not recreated as local files. Observation callbacks are configured outside the runtime. history.py and ChatService share one idempotent ConversationService archive writer.

Keep __init__.py files lightweight to avoid circular imports. Do not import a whole feature through core/models.py from runtime code. If the runtime needs a counter or policy input from another feature, inject a callable/value using native contracts.

### 7.3 Required legacy removals

| Remove | Replacement | Exit gate |
| --- | --- | --- |
| Custom model/tools nodes and graph routing loop | create_agent | Full chat/tool/domain regressions. |
| LLMClient, LLMResult, OpenAICompatibleClient | Native models/messages | Agent, summary, worker, health and fixture callers migrated. |
| Custom summary engine and new writes to old summary config | SummarizationMiddleware and native working state | Legacy import and archive integrity. |
| ObservedAgentRuntime and ObservedLLMClient | Native callbacks and explicit application events | Complete traces without duplicate spans. |
| ObservedAgentRepository | Explicit repository/application event boundaries | Approval and persistence-event coverage. |
| Automatic retry loops at migrated sites | Native tool/runnable retry components | Side-effect policy and attempt ceilings preserved. |
| Provider-wrapper token emitter | Native stream adapter | No duplicate deltas/internal text leakage. |
| persist_from positional slicing | Stable-ID reconciliation | Summary/resume/failure/multi-turn history tests. |
| Old core feature modules and migration-only re-exports | Direct feature imports | All callers moved; serializer/checkpoint compatibility verified; old work drained. |
| Temporary legacy selection/import scaffolding | One native execution path | Supported legacy data remains readable; old work is drained. |

Do not create permanent agent_model.py, middleware/observation.py or runtime facade wrappers that duplicate native interfaces. Temporary shims require a named consumer-removal gate.

## 8. Ordered implementation phases

Each phase produces a reviewable change and verification evidence. Production stays on the legacy runtime until the complete native path passes the release gates.

### Phase 0 — Baseline, dependencies, native feasibility

- Record the baseline and run focused existing regressions.
- Upgrade and lock a compatible package set including the chosen tool-error middleware.
- Prove native models/tools, message metadata, composition order, streaming and callback propagation.
- Prove built-in summarization with an external archive, mixed-batch HITL, saver recreation/resume and ordered tool effects.
- Inspect default summary trimming/failure and retry behavior; record exact APIs and any narrow gaps.

Exit: supported integration is demonstrated without private factory patching or reimplementing the loop/summary engine. A specific unresolved gap blocks affected-flow cutover.

### Phase 1A — Extract application and feature boundaries

- Move conversation, provider and observation models/helpers to their Section 7 owners; preserve serialized field names and public imports through temporary shims where necessary.
- Move InMemoryRepository to store/memory_repository.py and update PersistentRepository inheritance/imports without changing persistence behavior.
- Extract ConversationService, ModelProviderService and their consumed repository protocols; move the observation service and inject its storage dependencies.
- Introduce ChatService as the route-facing application coordinator. While migration is incomplete, it can delegate execution to the legacy implementation through an explicit temporary branch.
- Move host tool operations to services/platform_tools.py and update affected domain/worker imports.
- Update main.py and api/dependencies.py composition. Capture baseline import-boundary checks and a startup/composition smoke test.

Exit: extracted services work with injected repositories, API schemas/storage data remain compatible, and native runtime modules have no domain or persistence dependency. Do not move the entire old AgentRuntime unchanged into core/agent_runtime.

### Phase 1B — Native models, messages, tools and fixtures

- Add model_providers/factory.py with native model construction; migrate agent, document-worker, health and test consumers from LLMClient.
- Add core/agent_runtime/factory.py and reusable state, plus services/agent_integration/builder.py, application state, history conversion and native tools.
- Bind business middleware and callback dependencies outside the runtime; pass native components into the factory.
- Enable plain chat/one-tool scenarios through ChatService under a development rollout setting.

Exit: the new path uses native messages directly, has no legacy completion wrapper underneath, and passes model/provider/one-tool contracts and runtime import-isolation checks.

### Phase 2 — Native working memory, archive and summarization

- Introduce conversation-scoped state, stable IDs, one-time legacy import and version isolation.
- Add archive-before-compaction in services/agent_integration/history.py and final/paused/failed reconciliation through ChatService and ConversationService.
- Configure built-in summarization, native counting, prompt projection and selected-model budgets.
- Delete the custom compression engine once remaining consumers migrate.

Exit: new turns add only new input; native summaries persist between turns; original records survive; oversized/invalid summary inputs are handled explicitly; imports do not duplicate history.

### Phase 3 — Native limits, errors, retries and scope policy

- Configure native model limits, tool-error formatting and eligible retry middleware.
- Give retries one owner; migrate standalone retry consumers before removing gateway loops.
- Put reusable deduplication/order policies and output guards in core/agent_runtime/middleware; bind scope/authorization policy in services/agent_integration.
- Use native tool results/commands for AINA activation, widgets and domain completion.

Exit: attempts, error payloads, ordering, scopes and side effects meet the contract without a custom tool loop.

### Phase 4 — Native HITL and approval persistence

- Configure built-in HITL in services/agent_integration/approvals.py; persist its public approval/interrupt association through ConversationService.
- Adapt confirm/deny, validation, terminal denial and permission revalidation.
- Specify and test counter/reporting semantics across resumes and new turns.
- Handle supersession, partial writes, runtime recreation, missing checkpoints and uncertain effects.

Exit: original checkpointed calls resume correctly; stale approvals fail; denial makes no extra generation; uncertain operations never replay.

### Phase 5 — Native callbacks and streaming

- Add observability/callbacks.py for agent and standalone model paths, and services/agent_integration/streaming.py for application events; keep SSE formatting in api/chat.py.
- Keep transport instrumentation only for required fields native callbacks cannot expose.
- Move domain events to explicit owners and remove observation subclasses/proxies.
- Delete the legacy text emitter and filter internal model generations.

Exit: full observation/SSE coverage without duplicate records or deltas; observation_interceptors.py has no remaining consumer.

### Phase 6 — Full verification and controlled cutover

- Run required backend/frontend checks and persistent-storage integration tests.
- Compare engines with separate repositories, scripted native models and mocked tools.
- Run bounded real-model cases on dedicated data when credentials/quota are available.
- Enable controlled native turns/conversations while preserving ownership of paused work.

Exit: public/domain contracts pass; planned state/summary differences are documented; rollback and native approval restoration are demonstrated.

### Phase 7 — Remove redundant mechanisms

- Complete Section 7.3, drain old runs/approvals, and retire the legacy loop/selection path.
- Document the legacy-record import support window.
- Remove obsolete core feature modules/import shims after all caller and serialization gates pass; enforce the final dependency rules.
- Update the test matrix and native architecture/configuration.
- Justify every remaining custom hook with a product requirement or demonstrated framework gap.

Exit: one native implementation remains. Middleware wrapped around permanent legacy mechanisms does not satisfy completion.

### 8.1 File creation by phase

| Phase | Files introduced or moved | Existing owners changed |
| --- | --- | --- |
| 0 | Minimal feasibility tests retained in later suites | Dependencies and baseline fixtures |
| 1A | conversations/; provider models/service/contracts; observability/ moves; services/chat.py and platform_tools.py; store/memory_repository.py | main.py, API dependencies/routes, repository inheritance, domain imports, compatibility exports |
| 1B | model_providers/factory.py; core/agent_runtime/factory.py and state.py; integration builder/state/tools/history | Model consumers, native model fixtures, ChatService |
| 2 | Integration context/transcript hooks; runtime model_policy.py; optional shared token helper | Archive service, compression consumers, checkpoint lifecycle |
| 3 | Runtime tool_policy.py; integration tool/scope policies | Gateway retries, tool adapters, composition tests |
| 4 | Integration approvals.py; resume tests | ChatService, ConversationService and approval metadata |
| 5 | observability/callbacks.py; integration streaming.py | Observation events, API SSE and standalone-call integration |
| 6–7 | No additional architecture layer expected | Cutover, legacy deletion, compatibility cleanup and documentation |

New files should contain the responsibilities listed in Section 7. Extract code as its consumers migrate; avoid empty packages or a parallel generic service/repository framework.

## 9. Verification plan

### 9.1 Existing regression coverage

| Area | Tests under backend/tests/ |
| --- | --- |
| API, approval, iteration, SSE and background runs | test_chat_api.py |
| Invalid calls, retries, duplicates and truncation | test_agent_resilience.py |
| Compression, provider behavior and model selection | test_context_compression.py, test_llm_client.py, test_model_settings.py |
| Scope and remote protocols | test_widget_routing.py, test_aina_api.py, test_a2a_gateway.py |
| Domain tools and standalone model work | test_memory_aina.py, test_document_aina.py, test_document_edit_tasks.py, test_tasks.py |
| Authorization | test_auth.py, test_capability_security.py |
| Observation semantics | test_trace_details.py, test_observability_dual_write.py, test_observation_architecture.py, current interceptor tests |
| Persistence and run ownership | store/test_graph_checkpoint.py, store/test_persistent_repository_observability_unit.py, relevant storage suites |
| Live model behavior | Existing evals/ suites |

Port observable assertions when deleting legacy classes. Do not keep a class solely for an implementation-specific test, or discard regression evidence because its filename names the old implementation.

### 9.2 New focused integration checks

- Native binding preserves structured content, valid/invalid calls, finish reasons, usage and provider fallback behavior.
- Composition archives originals before summarization, budgets final projected requests, and rejects truncated calls before approval/execution.
- Legacy import runs once; native history survives turns without duplication; actor data stays isolated under concurrency.
- Summary failure preserves previous usable state; oversized indivisible results remain archived and do not reach the provider.
- Batch effect/widget order, deduplication, scope updates, state merging and cancellation remain deterministic.
- Native retries preserve physical attempt ceilings/idempotency keys; side-effecting operations receive no automatic replay.
- Mixed-risk HITL pauses all siblings; invalid risky arguments do not prompt; denial closes the batch without another model call.
- Saver/runtime recreation resumes the original calls; cross-actor, repeated or stale confirmations fail; missing state does not replay actions.
- New user input supersedes pending interrupts safely; deletion cleans checkpoint state under correct ownership.
- Callbacks cover conversation, summary, document and health calls without duplicate records or credential leakage.
- Native streaming emits one text sequence/terminal outcome, hides internal generations and preserves worker cleanup semantics.
- Partial archive/checkpoint writes reconcile by stable ID without duplication or side-effect replay.

Test real framework components with scripted native models and mocked external operations. Mocking away the middleware/agent pipeline would not validate this migration.

### 9.3 Architecture and service checks

- Enforce Section 7.2 using static import checks, including relative imports and compatibility barrels. core/agent_runtime may not import services, conversations, model_providers, observability, aina, store or api, directly or through a shared core helper.
- Run runtime composition/guard tests with scripted native models, native tools and an in-memory framework saver, without constructing HTTP routes, domain repositories or network services.
- Verify ChatService and extracted feature services accept protocol-compatible test repositories. conversations must not import ChatService or LangChain execution objects; protocols must not import their service implementations.
- Verify document workers and health calls construct native models through model_providers without importing agent_runtime.
- Smoke-test main.py dependency assembly and affected route injection. Check old and new model serializers preserve public JSON and durable record fields.
- Test integration adapters with real framework components: conversation archive hooks, AINA state, HITL, native callbacks and application event translation must be wired correctly across the package boundary.
- Update imports in regression tests and domain/storage consumers; remove compatibility aliases only after all callers and persisted type references are accounted for.

These are migration gates, not claims that these new tests already exist. Scope import enforcement to migrated packages; unrelated existing domains can adopt narrower repository contracts separately.

### 9.4 Commands and measurement

From backend, use Python 3.12 and the selected lock:

~~~powershell
uv sync --locked --extra dev --python 3.12
uv run --no-sync python -m pytest -q tests/test_chat_api.py tests/test_agent_resilience.py tests/test_context_compression.py tests/test_llm_client.py tests/test_model_settings.py
uv run --no-sync python -m pytest -q tests/test_capability_security.py tests/test_widget_routing.py tests/test_memory_aina.py tests/test_document_aina.py tests/test_document_edit_tasks.py tests/test_tasks.py
uv run --no-sync python -m pytest -q tests/test_trace_details.py tests/test_observation_architecture.py tests/store/test_graph_checkpoint.py
~~~

Add new files as phases introduce them. Before cutover, run the complete backend command in .github/workflows/quality-checks.yml, Ruff on changed Python files, and the required frontend build/browser gates. Run production-saver restoration and MySQL/Redis integration checks in the designated environment. Record unavailable checks and skips accurately.

Compare public status/output, tool arguments/results, external execution counts, archive completeness, native checkpoint behavior, usage, provider attempts, first-token latency and terminal events. Normalize generated IDs/timestamps only. Report expected extra summary calls from the new lifecycle separately from unexplained additional requests.

Use separate repositories/mocked tools for paired comparisons. Never shadow-run the same real side effect through two engines. Live cases use dedicated data, bounded quota and repeated trials where quality claims depend on variability.

## 10. Rollout and rollback

1. Retain the legacy production default until the complete native path meets its gates.
2. Use one server-owned rollout setting/cohort rule, never an untrusted client engine selector.
3. Persist runtime/checkpoint ownership of approvals. Old approvals stay on the old engine; native ones resume natively.
4. Import a legacy conversation only after unresolved legacy execution is drained; initialize native state once.
5. Monitor failures, approvals, duplicate execution, history integrity, streams, summary frequency, provider attempts and latency.
6. Drain old work, complete the removal checklist, and retire temporary selection/import support when its window closes.

Rollback disables native selection for new work. Existing native pauses require a compatible native worker to finish/cancel them. Quiescent conversations may return to legacy processing from the complete archive after confirming no pending/uncertain operation. Do not translate native checkpoints into old graph topology or replay side effects through the old engine.

Package moves must also account for serialization that records Python module/type names. Inspect the saver and persisted payloads; test restoration after moving types. Retain narrowly scoped import aliases only for supported old records/workers, and remove them when that compatibility window closes. Renaming modules alone must not rename database tables, storage keys or API fields.

Keep additive metadata backward-readable during the window. Actor exposure, execution before approval, duplicate effects, lost/orphaned messages, unrecoverable approvals and systematic stream/trace loss trigger rollback. Recovery tests must not assume atomicity across repository writes, checkpoints and external operations.

## 11. Definition of done

- [ ] create_agent owns the only production agent loop, assembled inside core/agent_runtime.
- [ ] Runtime code is independent of conversation/provider/observation services, AINA, concrete repositories and HTTP schemas; import checks enforce the boundary.
- [ ] ChatService owns orchestration; conversations, model_providers and observability own their data/services; store owns concrete repositories.
- [ ] Domain model moves preserve serialized fields, affected imports are migrated, and persisted type references/temporary shims have explicit retirement gates.
- [ ] Native models/messages serve chat, summary, document-worker and health calls; the custom completion protocol is removed.
- [ ] Built-in summarization owns compression and native checkpoints own working memory; the custom summary engine is removed.
- [ ] Built-in limits, HITL and tool-error handling are active with only documented product adapters.
- [ ] Native retries own migrated automatic retries without nested attempt multiplication or side-effect replay.
- [ ] Native callbacks replace agent/LLM/repository observation wrappers.
- [ ] Native streaming supplies chat deltas through one public-event adapter.
- [ ] Public API/archive schemas retain product data; native messages remain the execution representation.
- [ ] Batch ordering, scope, deduplication, supersession and run ownership retain their protections.
- [ ] Legacy history imports safely, originals survive compaction, and native approvals restore with the production saver.
- [ ] Required verification evidence is recorded, including unavailable optional live checks.
- [ ] Each custom mechanism has a concrete framework-gap or business-policy justification and a meaningful regression.
- [ ] Every removal item is complete or recorded as a narrow exception with a removal trigger.

## 12. Decisions requiring implementation evidence

| Decision | Default | Evidence |
| --- | --- | --- |
| Versions | Upgrade to a compatible stable set with the selected built-ins. | Locked installation/API and regression checks. |
| Package ownership | core/agent_runtime for execution; feature packages for business services; services for integration; store for concrete persistence. | Import checks, isolated runtime tests and composition smoke test. |
| Repository split | Move the existing aggregate implementation; extract only consumed feature protocols. | No behavior/data changes from the move; no concrete repository dependency in extracted services. |
| Models | Direct native instances from model_providers/factory.py; no permanent model wrapper. | Provider compatibility, actor settings, all callers migrated. |
| Summaries | Built-in engine plus configuration and narrow policy adapters. | No silent data loss; valid replacement; archive-before-compaction. |
| Memory | Conversation-scoped native checkpoints. | Idempotent import, ownership, deletion and pending-interrupt handling. |
| Ordered tools | Native configuration first; minimal public-hook sequencing if needed. | Ordered effects, correct state merge, no deadlocks/private patches. |
| Approval | Native HITL plus public mapping/terminal-denial policy. | Mixed batches, partial writes, restart/resume and stale confirmation. |
| Retries | Native mechanisms around single-attempt operations. | Side-effect restrictions and physical attempt ceilings unchanged. |
| Counters | Native canonical counters with explicit public reporting translation. | New-turn/resume reset behavior and documented configured limit scope. |
| Physical request records | Native callbacks; minimal transport instrumentation only for missing required fields. | Correct granularity, redaction and no double counting. |

Phase 0 is the first implementation deliverable. Completion requires native framework mechanisms, the service boundaries in Section 7, and removal of the redundant legacy execution path.
