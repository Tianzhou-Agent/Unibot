# Business database schema migration plan

Date: 2026-10-01 (scope revised 2026-10-13)

Status: Steps 1 (Alembic), 2 (conversations, messages, approvals), 3 (users), 4 (schedules, sandboxes), 5 (per-user records), 6 (registry) and 6b (run traces) implemented. Step 7 proposed.

Target: a public multi-tenant service running several backend replicas against one MySQL database. Every replica must see every other replica's writes immediately, so no business table may be served from a process-local copy.

## 1. Context

There is no production data. Local and dev databases can be dropped and recreated, so this plan has no backfill, rollback conversion, or maintenance-window steps.

Paths below are relative to `backend/src/tianzhou_agent_platform/`.

Business records were stored as generic `id / payload JSON / updated_at` rows. PR #22 (`7cf1069`) replaced the payload with one column per model field (`store/repository_schema.py`), but that was never the main problem. `PersistentRepository.initialize` loads every business table into process-local dictionaries at startup, and most reads are served from those dictionaries. This causes the concrete problems below:

| Problem | Where |
| --- | --- |
| Each message append rewrites the whole conversation, including its full history. | `append_messages`, `append_provider_messages` → `_save_record` |
| Email lookup and registration reload the entire users table. Uniqueness relies on a Redis lease, not the database. | `find_user_by_email`, `create_user`, `upsert_github_user`, `_refresh_users` |
| Execution history loads every row, then filters and limits in Python. | `list_scheduled_aina_executions`, `list_sandbox_executions` |
| Startup time and memory grow with all stored history. | `initialize` |
| Tables are created with `MetaData.create_all()`, which never alters existing tables. **Fixed in step 1.** | `store/database/mysql.py`, `create_tables` |
| A second replica serves stale records and overwrites the other replica's changes, because only users, workspaces and AINA projects are ever re-read from MySQL. | every `InMemoryRepository` method `PersistentRepository` does not override |

Changing the DDL alone fixes none of these. Each table that changes must also have its reads and writes moved to SQL.

## 2. Scope

**In scope:** every business table served by `PersistentRepository`. Each one stops being loaded into memory and is read and written in SQL. Section 3 lists the tables whose shape also changes; the rest keep their current columns.

**Schema management (step 1):** every MySQL table the application owns, including OBS, graph checkpoint, session-task and probe tables.

**Out of scope:** OBS, graph checkpoint and session-task *access code*, which already query MySQL directly.

## 3. Target tables

Rules:

- Scalar fields used for identity, ownership, filtering, or ordering become typed columns. Nested or variable data stays in named JSON columns. Migrated tables have no `payload` column.
- Times are stored as UTC `DATETIME(6)` and converted back to timezone-aware values on read.
- Long text (message content, scripts, stdout/stderr, errors) uses `MEDIUMTEXT` or larger, so there is no new 64 KiB limit.
- Keep the existing table names and `VARCHAR(255)` IDs.

| Table | Columns | JSON | Keys and indexes |
| --- | --- | --- | --- |
| `unibot_conversations` | `id`, `user_id`, `tenant_id`, `workspace_id`, `title`, `category`, `status`, `run_status`, `active_trace_id`, `run_error`, `run_started_at`, `primary_aina_id`, `last_aina_id`, `next_message_sequence`, `created_at`, `updated_at` | `config`, `enabled_ainas`, `active_aina_ids` | `(tenant_id, user_id, updated_at, id)` |
| **New** `unibot_messages` | `conversation_id`, `id`, `sequence_no`, `role`, `content`, `content_type`, `reasoning`, `tool_call_id`, `name`, `trace_id`, `created_at` | `tool_calls`, `widgets` | PK `(conversation_id, id)`; unique `(conversation_id, sequence_no)`; FK to conversations |
| `unibot_users` | `id`, `email`, `email_normalized`, `name`, `password_hash`, `github_id`, `github_login`, `avatar_url`, `tenant_id`, `created_at`, `updated_at` | — | Unique `email_normalized` (global, as today); unique nullable `github_id`; `(created_at, id)` |
| `unibot_scheduled_aina_executions` | `id`, `task_id`, `aina_id`, `user_id`, `tenant_id`, `trigger`, `scheduled_for`, `call_id`, `node_id`, `status`, `error`, `started_at`, `finished_at`, `duration_ms` | `input`, `result` | `(task_id, started_at, id)` |
| `unibot_sandbox_executions` | `id`, `sandbox_id`, `user_id`, `tenant_id`, `workspace_id`, `language`, `script`, `working_directory`, `status`, `stdout`, `stderr`, `exit_code`, `duration_ms`, `truncated`, `started_at`, `finished_at` | — | `(tenant_id, user_id, started_at, id)`; `sandbox_id` |

Notes:

- `email_normalized` is `email.strip().lower()`, matching the current Python lookup. Give it a binary or `utf8mb4_0900_as_cs` collation so the database does not add accent-insensitive matching.
- Store an empty `github_id` as `NULL` so the unique key ignores it, matching how the current check skips empty IDs.
- `duration_ms` is a float in the models, so it uses `DOUBLE`.

## 4. Repository changes

- The existing `row_values` / `model_from_row` converters stay for tables whose shape does not change; the five tables in section 3 get small hand-written converters.
- Stop loading each resource in `initialize` as its step lands. Their reads and writes go straight to MySQL. Remove the matching dictionary use inherited from `InMemoryRepository`, including authorization helpers and cleanup paths such as deleting a task's executions. `InMemoryRepository` stays as-is for tests.
- **Messages:** append in a single transaction. Lock the conversation row with `SELECT … FOR UPDATE`, skip IDs that already exist in that conversation (current retry behavior), insert the new rows in order, then advance `next_message_sequence` and `updated_at`. Read history ordered by `sequence_no`.
- **Public API unchanged:** `Conversation.messages` is still returned in full, rebuilt from rows. Lists that need messages load them in one batched query rather than one query per conversation. Internal run-state and ownership checks read conversation metadata only.
- **Users:** look up by `email_normalized` and `github_id` in SQL. Turn unique-key violations into the existing conflict errors and remove the `auth-user-write` Redis lease.
- **Executions:** filter, order, and limit in SQL.
- Keep the Redis conversation-run leases and heartbeats as they are. Finishing a run updates the row only if `active_trace_id` still matches the run's trace ID.

## 5. Schema management

Implemented in step 1:

- Alembic lives in `store/migrations/` (shipped in the wheel) with `backend/alembic.ini` for developers. Revision `0001` is a baseline of every application-owned table. Autogenerate ignores tables the application does not own.
- Deployments run `tianzhou-agent-platform-migrate` (a one-shot `migrate` compose service; a Job on Kubernetes). The app checks the revision at startup and refuses to start if it is not at head. No code path calls `create_all()` or hand-written `ALTER TABLE` any more; the OBS and session-task ad-hoc column upgrades are gone.
- Databases created by the release before step 1 already have the baseline schema: `tianzhou-agent-platform-migrate stamp 0001`. Older databases: drop the tables, then upgrade.

Adding Alembic now costs little and becomes expensive once production data exists.

## 6. Steps

| Step | Deliverable | Done when |
| --- | --- | --- |
| 1 ✅ | Alembic setup, baseline revision, startup revision check | A fresh database reaches `head`, the app starts on it, and it refuses to start on a database with the wrong revision. |
| 2 ✅ | Conversations, messages and approvals | Chat, approval resume, and archive tests pass. Concurrent and retried appends neither duplicate nor lose messages. Appends insert only new rows. |
| 3 ✅ | Users | Concurrent registration or GitHub linking with the same email gives one user and one conflict error. The Redis lease is removed. |
| 4 ✅ | Sandboxes, scheduled tasks and their execution history | History is filtered, ordered, and limited in SQL. Deleting a task or sandbox removes its executions. The scheduler reads tasks from SQL instead of reloading the table. |
| 5 ✅ | Per-user records: workspaces, memories, model providers, installations, feedbacks, document edit tasks, AINA projects | Owner/tenant filters run in SQL. Read-modify-write updates use a row lock or version check instead of a Redis lease. The scheduler records a run by updating only its run-state fields, so a user's edit made while the task runs is not overwritten (today it writes back the whole task as read at the start of the tick, on one node as well as several). |
| 6 ✅ | Shared registry: tools, skills, AINAs | Discovery and registration read SQL. Two replicas cold-starting on an empty database both start: registering the built-in AINAs is idempotent (it used to fail the second replica with a duplicate key). |
| 6b ✅ | Per-process run traces (observability follow-up) | With OBS on, a finished run's trace and LLM calls leave memory 15 minutes after it completes, and a cap evicts the oldest unfinished ones (never a running one); memory stays flat over many turns. Every reader already prefers OBS and falls back to the in-process trace, so the legacy trace endpoints stay as that fallback. Feedback submitted after the window names the answering agent from OBS. |
| 7 | Remove the in-memory state from `PersistentRepository` | It no longer inherits `InMemoryRepository` dictionaries other than the run-scoped traces handled by 6b. A two-replica test on one database shows every resource's writes on the other replica immediately. |

Each step is its own PR, with an Alembic revision when the schema changes.

Known limits after step 5: the per-actor caps (20 model providers, 500 memories) and memory de-duplication read before inserting, so simultaneous creates on different replicas can exceed a cap by a few or store one duplicate memory. Making them exact needs a per-actor lock row; not done because the cost of either outcome is small.

Sign-in through Google, Microsoft and Apple (added on main in #37) keeps its identities in the `oidc_identities` JSON column, which carries no unique key; the account's email, which is unique, stops concurrent first sign-ins from creating two users. Huawei ids have a unique key like GitHub ids (migration `0006`). A column or table per external identity would make the guarantee independent of the email.

Follow-up outside this plan: `GET /conversations` and the admin conversation endpoints return every conversation with its full message history. Step 2 loads those messages in one batched query, but the endpoints should page and return summaries without messages.

## 7. Testing

- Extend the existing opt-in real-MySQL storage tests (`backend/docker-compose.storage.yml`, `backend/tests/store/test_storage_e2e.py`). Constraints, collation, and row locking need real MySQL 8.0.
- Round-trip every model field through the converters: null vs empty values, Unicode, microsecond timestamps, long content, and widgets/tool calls.
- Locking reads (`SELECT … FOR UPDATE`) only on rows known to exist. On a missing row InnoDB takes a gap lock, and two transactions that both insert into that gap deadlock; let a unique key decide insert races instead (found by the step 3 GitHub sign-in race test).
- A migration validates existing data before its first DDL statement. MySQL cannot roll DDL back, so a check that fails after `ALTER TABLE` leaves a half-applied revision that cannot be rerun.
- Run concurrency tests with two repository instances on one database: same-email registration, simultaneous message appends, a stale run-finish call, and a write on one instance read back on the other for every resource.
- `backend/tests/store/conftest.py` migrates each opted-in live database to head; `test_migrations.py` covers the baseline on a disposable database.
- Run the existing repository behavior tests against both the in-memory and persistent repositories.
