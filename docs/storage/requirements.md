# Storage Layer Requirements

## Scope

This document defines the requirements for the Unibot backend storage layer. The storage layer lives under `backend/src/tianzhou_agent_platform/store` and provides local repository and storage functions to API, core, AINA, and other upper application layers.

The first implementation shall focus on simple, stable, single-operation storage contracts before business-specific repositories or advanced transaction boundaries are added.

## Terms

- Storage layer: the local backend module that exposes repository and storage primitives.
- Storage adapter: a local implementation backed by MySQL, Redis, S3-compatible object storage, or filesystem/NAS storage.
- Adapter route: a configuration rule that maps a namespace to the adapter responsible for that namespace.
- Atomic operation: a single storage-layer function call that delegates atomic persistence to the configured backend storage system.
- Resource: an addressable stored item identified by namespace and key.
- Caller layer: any upper application module that calls the storage layer through local Python functions, including API routes, core services, AINA modules, tools, memory, and background jobs.

## Assumptions And Boundaries

- The storage layer shall be used through local Python function calls inside the backend process.
- The storage layer shall not be implemented or consumed as an HTTP, RPC, or standalone network service.
- The first implementation shall expose single-operation atomic storage functions only.
- The first implementation shall provide concrete adapters for MySQL, Redis, S3-compatible object storage, and filesystem/NAS storage.
- The generic resource contract shall use namespace and key addressing with opaque payloads, content type, and metadata.
- The generic resource contract shall not provide filtering, querying, joins, secondary indexes, or business-specific structured schemas.
- Business-specific repositories may define structured database schemas later, but those schemas are outside this generic storage contract.
- The storage layer shall rely on configured production storage systems for distributed consistency, durability, replication, and availability.
- The storage layer shall not implement distributed locks, distributed consensus, version tokens, compare-and-swap, public transaction boundaries, or cross-backend distributed transactions.
- The storage layer shall not encode business-specific schemas, prompts, agent state transitions, or UI concepts.

## Requirements

### Requirement FR-1: Local Storage Interface

**Type:** Functional Requirement

**User Story:** As a caller layer developer, I want a stable asynchronous local storage interface, so that upper application modules can persist data without depending on concrete storage backend clients.

#### Acceptance Criteria

1. WHEN a caller layer imports the storage module THEN the storage layer SHALL expose typed asynchronous Python interfaces under `tianzhou_agent_platform.store`.
2. WHEN a caller layer invokes a storage operation THEN the storage layer SHALL execute through local Python function calls inside the backend process.
3. IF a caller layer attempts to use the storage layer through HTTP or RPC THEN the storage layer SHALL provide no such network API in the first implementation.
4. WHEN a caller layer performs a low-level resource operation THEN the storage layer SHALL require an explicit namespace and key.
5. WHEN a caller layer stores a resource THEN the storage layer SHALL accept payload, content type, metadata, and optional time-to-live values as separate fields.
6. WHEN an operation supports missing-resource tolerance THEN the storage layer SHALL expose an explicit `missing_ok` parameter for that operation.
7. WHEN a storage operation completes successfully THEN the storage layer SHALL return the committed value or acknowledgement without exposing backend-specific implementation details.

### Requirement FR-2: Basic Resource Operations

**Type:** Functional Requirement

**User Story:** As a caller layer developer, I want basic resource operations, so that upper application modules can create, read, replace, delete, and list stored resources.

#### Acceptance Criteria

1. WHEN `get` is requested for an existing resource THEN the storage layer SHALL return the stored payload, metadata, and content type.
2. WHEN `exists` is requested for a resource THEN the storage layer SHALL return whether the resource is currently present.
3. WHEN `create` is requested for a missing resource THEN the storage layer SHALL persist the resource atomically.
4. IF `create` is requested for a resource that already exists THEN the storage layer SHALL fail with an already-exists error.
5. WHEN `put` is requested for a resource THEN the storage layer SHALL replace the entire resource payload and metadata atomically.
6. WHEN `delete` is requested for an existing resource THEN the storage layer SHALL remove the resource atomically.
7. IF `get` targets a missing resource and `missing_ok` is false THEN the storage layer SHALL fail with a not-found error.
8. IF `get` targets a missing resource and `missing_ok` is true THEN the storage layer SHALL return an empty result.
9. IF `delete` targets a missing resource and `missing_ok` is false THEN the storage layer SHALL fail with a not-found error.
10. IF `delete` targets a missing resource and `missing_ok` is true THEN the storage layer SHALL return a successful acknowledgement without mutating storage.
11. WHEN `list` is requested for a namespace on an adapter that supports ordered listing THEN the storage layer SHALL return resources ordered lexicographically by key.
12. IF a list result exceeds the adapter page limit THEN the storage layer SHALL return a pagination token for retrieving the next page.
13. IF the selected adapter cannot provide ordered listing for the namespace THEN the storage layer SHALL fail with an unsupported-operation error.

### Requirement FR-3: Adapter Routing

**Type:** Functional Requirement

**User Story:** As a caller layer developer, I want namespace-based adapter routing, so that caller layers can use one storage interface while the backend selects the correct configured adapter.

#### Acceptance Criteria

1. WHEN a storage operation is invoked for a namespace THEN the storage layer SHALL resolve the selected adapter from namespace-to-adapter routing configuration.
2. IF a namespace has an explicit adapter route THEN the storage layer SHALL use the adapter configured for that namespace.
3. IF a namespace has no explicit adapter route and a default adapter route is configured THEN the storage layer SHALL use the default adapter.
4. IF a namespace has no explicit adapter route and no default adapter route is configured THEN the storage layer SHALL fail with an unknown-namespace error.
5. WHEN adapter routing is resolved THEN the storage layer SHALL not require caller layers to import or instantiate backend-specific clients.

### Requirement FR-4: Backend Adapter Delegation

**Type:** Functional Requirement

**User Story:** As a caller layer developer, I want storage operations to delegate to the configured backend adapter, so that each storage backend is used according to its native purpose and capability.

#### Acceptance Criteria

1. WHEN a storage adapter is configured THEN the storage layer SHALL route matching operations to that adapter.
2. WHERE the MySQL adapter is configured for the generic resource contract THEN the storage layer SHALL use MySQL-backed key-addressed durable resource operations.
3. WHERE the Redis adapter is configured THEN the storage layer SHALL use Redis-backed operations for cache, key-value, and TTL data.
4. WHERE the S3 adapter is configured THEN the storage layer SHALL use S3 provider object operations for object storage.
5. WHERE the filesystem/NAS adapter is configured THEN the storage layer SHALL use filesystem/NAS operations for file storage.
6. IF a requested operation is unsupported by the selected adapter THEN the storage layer SHALL fail with an unsupported-operation error before attempting the operation.
7. IF a time-to-live value is supplied for an adapter that does not support TTL THEN the storage layer SHALL fail with an unsupported-operation error rather than silently ignoring the TTL.

### Requirement FR-5: Adapter Initialization And Resource Management

**Type:** Functional Requirement

**User Story:** As a backend application developer, I want storage adapters to initialize and release local resources consistently, so that caller layers can use repository functions safely inside the backend process.

#### Acceptance Criteria

1. WHEN the backend application starts THEN the storage layer SHALL validate configured adapters and adapter routes before accepting storage operations.
2. WHEN the backend application starts THEN the storage layer SHALL initialize required local clients, connection pools, or filesystem paths for configured adapters.
3. IF a required storage adapter is misconfigured THEN the storage layer SHALL fail startup validation with actionable configuration errors.
4. IF an adapter route references an unconfigured adapter THEN the storage layer SHALL fail startup validation with an actionable routing error.
5. WHEN the backend application shuts down THEN the storage layer SHALL close local clients and connection pools owned by the storage layer.
6. WHILE a required adapter is unavailable THEN the storage layer SHALL reject operations targeting that adapter with an unavailable error.

### Requirement NFR-1: Single-Operation Atomicity And Consistency

**Type:** Non-Functional Requirement

**User Story:** As a caller layer developer, I want each storage-layer operation to have clear atomicity and consistency behavior, so that upper application modules can rely on storage results without compensating for partial writes.

#### Acceptance Criteria

1. WHEN a storage mutation succeeds THEN the storage layer SHALL make the complete mutation visible to subsequent reads according to the selected backend adapter's consistency guarantees.
2. IF a storage mutation fails before the backend commit completes THEN the storage layer SHALL leave no partial mutation visible through the storage interface where the selected backend supports atomic commit behavior.
3. IF the selected backend cannot provide atomic behavior for a requested mutation THEN the storage layer SHALL reject the operation with an unsupported-operation error.
4. WHILE concurrent callers write the same namespace and key THEN the storage layer SHALL use last-writer-wins semantics according to the selected backend adapter's commit order.
5. WHEN binary payloads are stored THEN the storage layer SHALL preserve the exact byte sequence returned by subsequent reads.
6. WHEN metadata is stored THEN the storage layer SHALL preserve metadata keys and values without mixing them into the resource payload.

### Requirement NFR-2: Error Handling And Timeouts

**Type:** Non-Functional Requirement

**User Story:** As a caller layer developer, I want consistent storage-layer errors and configurable timeouts, so that upper application modules can handle failures without depending on backend-specific exception types.

#### Acceptance Criteria

1. IF a storage adapter raises a backend-specific error THEN the storage layer SHALL translate it into a storage-layer error type before returning it to caller layers.
2. WHEN adapter configuration is loaded THEN the storage layer SHALL apply configured operation timeout values or documented defaults.
3. IF a storage operation times out THEN the storage layer SHALL return a timeout error that includes the operation name, namespace, adapter name, and retryability.
4. WHEN the storage layer returns an error THEN the storage layer SHALL not expose secrets, credentials, connection strings, or raw backend exception payloads.
5. WHEN an adapter rejects an operation due to unsupported functionality THEN the storage layer SHALL return an unsupported-operation error distinct from not-found and backend-unavailable errors.

### Requirement NFR-3: Observability

**Type:** Non-Functional Requirement

**User Story:** As an operator, I want storage operations to produce basic operational signals, so that storage behavior can be monitored and production issues can be diagnosed.

#### Acceptance Criteria

1. WHEN a storage operation starts THEN the storage layer SHALL record adapter name, operation name, namespace, and correlation identifier when available in structured logs.
2. WHEN a storage operation completes THEN the storage layer SHALL record success or failure, duration, and error category in structured logs.
3. IF application metrics are configured THEN the storage layer SHALL emit metrics for operation count, latency, failures, and timeouts.

### Requirement NFR-4: Security And Data Integrity

**Type:** Non-Functional Requirement

**User Story:** As a platform maintainer, I want storage access to validate namespaces, keys, payload limits, and sensitive data handling, so that stored data remains isolated and protected.

#### Acceptance Criteria

1. WHEN a caller layer calls the storage layer THEN the storage layer SHALL validate the namespace before accessing adapter resources.
2. IF a key contains path traversal, invalid separators, or reserved control characters THEN the storage layer SHALL reject the key before it reaches an adapter.
3. WHEN a caller layer stores a payload THEN the storage layer SHALL validate the payload size against configured limits before writing to the selected adapter.
4. IF a payload exceeds the configured limit THEN the storage layer SHALL reject the operation with a payload-too-large error.
5. WHERE encryption at rest is configured on a backend storage system THEN the storage layer SHALL use that backend's configured client or connection settings and SHALL NOT add application-layer encryption in the first implementation.
6. WHEN structured logs or errors are emitted THEN the storage layer SHALL exclude payload contents unless explicitly configured for a safe local development mode.

### Requirement NFR-5: Contract Testing

**Type:** Non-Functional Requirement

**User Story:** As a backend maintainer, I want shared tests for storage adapters, so that adapter behavior stays consistent as implementations evolve.

#### Acceptance Criteria

1. WHEN an adapter is added THEN the storage layer SHALL provide contract tests for the common operations supported by that adapter.
2. WHEN an adapter supports `create`, `get`, `put`, `delete`, or `list` THEN the contract test suite SHALL verify the supported operation behavior.
3. WHEN an adapter supports TTL THEN the contract test suite SHALL verify expiration behavior.
4. IF an adapter supports binary payloads THEN the contract test suite SHALL verify byte-for-byte round trips.
5. WHEN storage errors are raised by adapters in tests THEN the contract test suite SHALL verify translation into storage-layer error types.
