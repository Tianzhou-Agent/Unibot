# Storage Layer Implementation Plan

- [ ] 1. Set up storage package foundations
  - Create the `tianzhou_agent_platform.store` package modules for models, errors, settings, validation, routing, facade, and adapters.
  - Export only local Python APIs from the package; do not add HTTP or RPC routes.
  - _Requirements: FR-1 AC1, FR-1 AC2, FR-1 AC3_

- [x] 1.1 Implement core storage data models
  - Create `StorageObject`, `StorageObjectSummary`, `StoragePage`, `StorageAck`, and internal `StorageWrite` models.
  - Add model tests for payload bytes, content type, metadata, acknowledgement fields, and paged result shape.
  - _Requirements: FR-1 AC5, FR-1 AC7, FR-2 AC1, FR-2 AC12, NFR-1 AC5, NFR-1 AC6_

- [x] 1.2 Implement storage error types
  - Create the shared storage error hierarchy from the design document.
  - Add tests that verify each concrete storage error is catchable as `StorageError`.
  - _Requirements: NFR-2 AC1, NFR-2 AC3, NFR-2 AC5_

- [x] 1.3 Implement storage settings models
  - Create `StorageSettings` and adapter-specific settings using `pydantic-settings`.
  - Include adapters, routes, default adapter, timeout, payload limit, default page size, max page size, and backend encryption-related client settings.
  - Add settings tests for defaults, invalid routes, and missing required adapter configuration.
  - _Requirements: FR-3 AC1, FR-3 AC2, FR-3 AC3, FR-3 AC4, FR-5 AC1, FR-5 AC3, FR-5 AC4, NFR-2 AC2, NFR-4 AC3, NFR-4 AC5_

- [x] 2. Implement validation and serialization helpers
  - Build the reusable validation module used by the facade and adapters.
  - Keep validators independent from concrete backend clients.
  - _Requirements: FR-1 AC4, FR-1 AC5, NFR-4 AC1, NFR-4 AC2, NFR-4 AC3, NFR-4 AC4_

- [x] 2.1 Implement namespace and key validation
  - Enforce namespace and key rules from the design document.
  - Add tests for valid names, path traversal, separators, control characters, empty keys, and length limits.
  - _Requirements: FR-1 AC4, NFR-4 AC1, NFR-4 AC2_

- [x] 2.2 Implement content type, metadata, payload, TTL, and page-size validation
  - Enforce MIME-style content type, metadata key/value limits, reserved `tzap-` prefix, payload size limit, positive-second TTL, and page-size bounds.
  - Add tests for invalid content types, metadata violations, oversized payloads, invalid TTLs, and page-size normalization.
  - _Requirements: FR-1 AC5, FR-2 AC12, FR-4 AC7, NFR-4 AC3, NFR-4 AC4_

- [x] 2.3 Implement binary envelope codec
  - Implement the `TZSTOR1` binary envelope encoder/decoder for Redis and NAS resources.
  - Add tests for byte-for-byte payload round trips, metadata preservation, corrupt envelope rejection, and content type preservation.
  - _Requirements: NFR-1 AC5, NFR-1 AC6, NFR-5 AC4_

- [x] 3. Implement adapter protocol, routing, and facade lifecycle
  - Create the shared async adapter protocol and route resolver.
  - Implement startup and shutdown orchestration for configured adapters.
  - _Requirements: FR-3 AC1, FR-3 AC5, FR-4 AC1, FR-5 AC1, FR-5 AC2, FR-5 AC5_

- [x] 3.1 Implement namespace route resolver
  - Resolve explicit namespace routes, default adapter fallback, unknown namespace errors, and routes referencing missing adapters.
  - Add unit tests for each routing branch.
  - _Requirements: FR-3 AC1, FR-3 AC2, FR-3 AC3, FR-3 AC4, FR-5 AC4_

- [x] 3.2 Implement adapter lifecycle manager
  - Initialize configured local clients, connection pools, and filesystem paths.
  - Close owned resources on shutdown and track adapter availability for facade calls.
  - Add tests with fake adapters for startup, shutdown, misconfiguration, and unavailable adapter behavior.
  - _Requirements: FR-5 AC1, FR-5 AC2, FR-5 AC3, FR-5 AC5, FR-5 AC6_

- [x] 4. Implement storage facade behavior
  - Implement the public async storage API around validation, routing, adapter calls, timeout handling, logging, and error normalization.
  - Keep caller code independent from concrete adapter clients.
  - _Requirements: FR-1 AC1, FR-1 AC2, FR-1 AC7, FR-3 AC5, NFR-2 AC1, NFR-3 AC1, NFR-3 AC2_

- [x] 4.1 Implement `get`, `exists`, `create`, `put`, `delete`, and `list`
  - Route all operations through the selected adapter after validation.
  - Implement `put` as unconditional full replacement with last-writer-wins semantics.
  - Add facade tests using fake adapters for successful CRUD and listing.
  - _Requirements: FR-2 AC1, FR-2 AC2, FR-2 AC3, FR-2 AC5, FR-2 AC6, FR-2 AC11, FR-4 AC1, NFR-1 AC4_

- [x] 4.2 Implement `missing_ok` behavior
  - Return `None` for missing `get(..., missing_ok=True)` and successful acknowledgement for missing `delete(..., missing_ok=True)`.
  - Raise not-found errors for missing resources when `missing_ok` is false.
  - Add tests for all missing-resource branches.
  - _Requirements: FR-1 AC6, FR-2 AC7, FR-2 AC8, FR-2 AC9, FR-2 AC10_

- [x] 4.3 Implement timeout and backend error normalization
  - Wrap adapter operations with the configured timeout.
  - Translate backend, timeout, unsupported-operation, unavailable, already-exists, not-found, and payload-too-large conditions into storage-layer errors.
  - Add tests that ensure raw backend exception messages and secrets are not exposed.
  - _Requirements: FR-4 AC6, FR-4 AC7, NFR-1 AC3, NFR-2 AC1, NFR-2 AC2, NFR-2 AC3, NFR-2 AC4, NFR-2 AC5, NFR-4 AC4_

- [x] 4.4 Implement structured logging and optional metrics hooks
  - Log operation start and completion with adapter name, operation name, namespace, duration, and error category.
  - Add optional metrics hook support for operation count, latency, failures, and timeouts.
  - Add tests that payload contents are excluded from logs.
  - _Requirements: NFR-3 AC1, NFR-3 AC2, NFR-3 AC3, NFR-4 AC6_

- [ ] 5. Build shared adapter contract tests
  - Create reusable async contract tests that every adapter test suite can apply to its supported operations.
  - Include a fake in-memory adapter to validate the contract harness early.
  - _Requirements: NFR-5 AC1, NFR-5 AC2, NFR-5 AC4, NFR-5 AC5_

- [ ] 5.1 Implement common CRUD contract tests
  - Test `create`, `get`, `exists`, `put`, `delete`, and `list` behavior against a contract adapter fixture.
  - Include duplicate create, full replace, deletion, ordered list, pagination, and binary round-trip cases.
  - _Requirements: FR-2 AC1, FR-2 AC2, FR-2 AC3, FR-2 AC4, FR-2 AC5, FR-2 AC6, FR-2 AC11, FR-2 AC12, NFR-1 AC5, NFR-5 AC2, NFR-5 AC4_

- [ ] 5.2 Implement capability and error contract tests
  - Test unsupported listing, unsupported TTL, atomic-create rejection or unsupported fallback, and adapter error translation.
  - Include tests for last-writer-wins behavior where the adapter can be exercised deterministically.
  - _Requirements: FR-2 AC13, FR-4 AC6, FR-4 AC7, NFR-1 AC1, NFR-1 AC2, NFR-1 AC3, NFR-1 AC4, NFR-2 AC1, NFR-5 AC3, NFR-5 AC5_

- [ ] 6. Implement MySQL adapter
  - Create the MySQL adapter module using SQLAlchemy Core and `mysql+aiomysql`.
  - Implement the generic key-addressed resource table and adapter methods.
  - _Requirements: FR-4 AC2, FR-5 AC2, NFR-1 AC1, NFR-1 AC2_

- [ ] 6.1 Implement MySQL schema metadata and startup checks
  - Define SQLAlchemy table metadata for `storage_resources`.
  - Add adapter startup validation for engine creation and required table availability or creation helper behavior.
  - Add unit tests for schema metadata and startup failure translation.
  - _Requirements: FR-4 AC2, FR-5 AC1, FR-5 AC2, FR-5 AC3, NFR-2 AC1_

- [ ] 6.2 Implement MySQL CRUD and list operations
  - Implement insert-only `create`, upsert-style `put`, `get`, `exists`, `delete`, ordered `list`, and pagination by resource key.
  - Reject TTL with `UnsupportedOperationError`.
  - Add MySQL adapter tests gated by MySQL environment variables.
  - _Requirements: FR-2 AC1, FR-2 AC2, FR-2 AC3, FR-2 AC4, FR-2 AC5, FR-2 AC6, FR-2 AC11, FR-2 AC12, FR-4 AC2, FR-4 AC7, NFR-1 AC5, NFR-1 AC6_

- [ ] 7. Implement Redis adapter
  - Create the Redis adapter module using `redis.asyncio`.
  - Use the shared binary envelope codec for stored values.
  - _Requirements: FR-4 AC3, FR-5 AC2, NFR-1 AC5, NFR-1 AC6_

- [ ] 7.1 Implement Redis CRUD and TTL operations
  - Implement `create`, `get`, `exists`, `put`, `delete`, and TTL expiration using Redis primitives.
  - Return `UnsupportedOperationError` for ordered `list` unless a sorted index is implemented.
  - Add Redis adapter tests gated by Redis environment variables.
  - _Requirements: FR-2 AC1, FR-2 AC2, FR-2 AC3, FR-2 AC4, FR-2 AC5, FR-2 AC6, FR-2 AC13, FR-4 AC3, FR-4 AC7, NFR-5 AC3_

- [ ] 8. Implement S3 adapter
  - Create the S3 adapter module using `boto3` wrapped with `asyncio.to_thread`.
  - Implement safe object key mapping and reserved metadata prefix handling.
  - Pass backend encryption-related settings through to the configured S3 client without adding application-layer encryption.
  - _Requirements: FR-4 AC4, FR-5 AC2, NFR-4 AC2, NFR-4 AC5, NFR-4 AC6_

- [ ] 8.1 Implement S3 CRUD, ordered list, and conditional create
  - Implement `get`, `exists`, `put`, `delete`, ordered `list`, and pagination over provider object listing.
  - Implement atomic `create` with provider conditional write support or raise `UnsupportedOperationError`.
  - Add S3 adapter tests gated by S3 environment variables.
  - _Requirements: FR-2 AC1, FR-2 AC2, FR-2 AC3, FR-2 AC4, FR-2 AC5, FR-2 AC6, FR-2 AC11, FR-2 AC12, FR-4 AC4, FR-4 AC6, NFR-1 AC1, NFR-1 AC2, NFR-1 AC3, NFR-1 AC5, NFR-1 AC6_

- [ ] 9. Implement NAS/filesystem adapter
  - Create the NAS adapter module using `aiofiles`, the shared binary envelope codec, and filesystem commit helpers.
  - Implement safe path mapping without exposing path traversal.
  - _Requirements: FR-4 AC5, FR-5 AC2, NFR-4 AC2, NFR-1 AC5, NFR-1 AC6_

- [ ] 9.1 Implement NAS CRUD, ordered list, and atomic create
  - Implement `get`, `exists`, `put`, `delete`, ordered `list`, and pagination over sorted filesystem keys.
  - Implement `put` with temp-file plus `os.replace` and `create` with atomic hard-link creation or unsupported fallback.
  - Add NAS tests using temporary directories.
  - _Requirements: FR-2 AC1, FR-2 AC2, FR-2 AC3, FR-2 AC4, FR-2 AC5, FR-2 AC6, FR-2 AC11, FR-2 AC12, FR-4 AC5, FR-4 AC6, NFR-1 AC1, NFR-1 AC2, NFR-1 AC3, NFR-1 AC5, NFR-1 AC6_

- [ ] 10. Wire package exports and automated verification
  - Export facade functions, models, settings, and errors from `tianzhou_agent_platform.store`.
  - Add automated tests that import the public package from caller-style code without adapter-client imports.
  - _Requirements: FR-1 AC1, FR-1 AC2, FR-1 AC3, FR-3 AC5_

- [ ] 10.1 Add facade-level integration tests with configured fake adapters
  - Test namespace routing across multiple adapters through the public facade.
  - Test validation, routing, timeout, logging, metrics hooks, and normalized errors together.
  - _Requirements: FR-1 AC7, FR-3 AC1, FR-3 AC2, FR-3 AC3, FR-3 AC4, FR-4 AC1, NFR-2 AC1, NFR-2 AC3, NFR-3 AC1, NFR-3 AC2, NFR-3 AC3_

- [ ] 10.2 Add final storage test suite checks
  - Add pytest markers or skips for MySQL, Redis, and S3 external-service adapter tests.
  - Ensure NAS and fake-adapter tests run without external services.
  - Verify all common contract tests are applied to each adapter according to supported operations.
  - _Requirements: NFR-5 AC1, NFR-5 AC2, NFR-5 AC3, NFR-5 AC4, NFR-5 AC5_
