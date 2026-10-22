"""Conversations, messages and approvals in SQL, shared by several repository instances (replicas).

Runs on SQLite; row locking under real concurrency is covered by ``test_conversation_store_mysql.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tianzhou_agent_platform.aina.protocol.widgets import WidgetDefinition
from tianzhou_agent_platform.conversations.models import ConversationCreate, ConversationUpdate, Message
from tianzhou_agent_platform.conversations.schemas import ApprovalRecord
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.store.lifecycle import StorageStores
from tianzhou_agent_platform.store.repository import PersistentRepository
from tests.support.sqlite_store import sqlite_session_factory


class _SqlOnly:
    def __init__(self) -> None:
        self.session_factory = sqlite_session_factory()


def _replicas() -> tuple[PersistentRepository, PersistentRepository]:
    stores = StorageStores(mysql=_SqlOnly(), redis=None, nas=None)  # type: ignore[arg-type]
    return PersistentRepository(stores), PersistentRepository(stores)


async def test_every_change_is_visible_to_the_other_replica() -> None:
    first, second = _replicas()
    conversation = await first.create_conversation(ConversationCreate(user_id="u1", tenant_id="t1", title="one"))

    assert (await second.get_conversation(conversation.id)).title == "one"

    await second.append_provider_messages(conversation.id, [{"role": "user", "content": "hi"}], trace_id="tr1")
    await first.update_conversation(conversation.id, ConversationUpdate(title="renamed"))
    await second.bind_conversation_aina(conversation.id, "aina.docs", mark_used=True)

    seen = await first.get_conversation(conversation.id)
    assert [message.content for message in seen.messages] == ["hi"]
    assert seen.title == "renamed"
    assert seen.active_aina_ids == ["aina.docs"]
    assert seen.primary_aina_id == seen.last_aina_id == "aina.docs"
    assert [item.id for item in await second.list_conversations(user_id="u1", tenant_id="t1")] == [conversation.id]


async def test_list_filters_and_orders_by_latest_change() -> None:
    first, second = _replicas()
    older = await first.create_conversation(ConversationCreate(user_id="u1", tenant_id="t1", category="work"))
    newer = await first.create_conversation(ConversationCreate(user_id="u1", tenant_id="t1"))
    await first.create_conversation(ConversationCreate(user_id="u2", tenant_id="t1"))
    deleted = await first.create_conversation(ConversationCreate(user_id="u1", tenant_id="t1"))
    await first.set_conversation_status(deleted.id, "deleted")

    assert [item.id for item in await second.list_conversations(user_id="u1", tenant_id="t1")] == [newer.id, older.id]
    await second.append_provider_messages(older.id, [{"role": "user", "content": "bump"}], trace_id="tr")
    assert [item.id for item in await second.list_conversations(user_id="u1", tenant_id="t1")] == [older.id, newer.id]
    assert [item.id for item in await second.list_conversations(category="work")] == [older.id]

    with pytest.raises(PlatformError):
        await second.get_conversation(deleted.id)
    assert (await second.get_conversation(deleted.id, include_deleted=True)).status == "deleted"


async def test_retried_append_stores_each_message_once_in_order() -> None:
    first, second = _replicas()
    conversation = await first.create_conversation(ConversationCreate(user_id="u1", tenant_id="t1"))
    batch = [Message(id="m1", role="user", content="a"), Message(id="m2", role="assistant", content="b")]

    await first.append_messages(conversation.id, batch)
    retried = await second.append_messages(conversation.id, [*batch, Message(id="m3", role="user", content="c")])

    assert retried is not None
    assert [message.id for message in retried.messages] == ["m1", "m2", "m3"]
    assert await first.append_messages("conv_missing", batch) is None


async def test_message_fields_round_trip() -> None:
    first, second = _replicas()
    conversation = await first.create_conversation(ConversationCreate(user_id="u1", tenant_id="t1"))
    created_at = datetime(2026, 10, 13, 1, 2, 3, 456789, tzinfo=UTC)
    message = Message(
        id="m1",
        role="assistant",
        content="长内容" * 30_000,  # beyond a 64 KiB TEXT column
        content_type="widget",
        reasoning="thinking first",
        tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}],
        name="assistant",
        widgets=[WidgetDefinition(id="w1", kind="markdown", title="Done", markdown="# Done")],
        trace_id="tr1",
        created_at=created_at,
    )
    tool_result = Message(id="m2", role="tool", content="", tool_call_id="call_1")

    await first.append_messages(conversation.id, [message, tool_result])

    stored = (await second.get_conversation(conversation.id)).messages
    assert stored == [message, tool_result.model_copy(update={"created_at": stored[1].created_at})]


async def test_provider_messages_require_an_active_conversation() -> None:
    first, _ = _replicas()
    conversation = await first.create_conversation(ConversationCreate(user_id="u1", tenant_id="t1"))
    await first.set_conversation_status(conversation.id, "archived")

    with pytest.raises(PlatformError) as error:
        await first.append_provider_messages(conversation.id, [{"role": "user", "content": "x"}], trace_id="tr")
    assert error.value.code == "RESOURCE_NOT_FOUND"


async def test_approvals_are_shared_and_only_pending_ones_are_cancelled() -> None:
    first, second = _replicas()
    conversation = await first.create_conversation(ConversationCreate(user_id="u1", tenant_id="t1"))
    other = await first.create_conversation(ConversationCreate(user_id="u1", tenant_id="t1"))

    def approval(approval_id: str, conversation_id: str) -> ApprovalRecord:
        return ApprovalRecord(
            id=approval_id,
            conversation_id=conversation_id,
            user_id="u1",
            tenant_id="t1",
            trace_id=f"trace_{approval_id}",
            tool_calls=[{"id": "call", "function": {"name": "write"}}],
            capability_names=["write"],
            runtime_ref={"thread": "lc-v2:x"},
            run_generation=2,
        )

    for item in (approval("a1", conversation.id), approval("a2", conversation.id), approval("a3", other.id)):
        await first.create_approval(item)
    await second.set_approval_status("a1", "approved")

    assert (await first.get_approval("a1")).status == "approved"
    assert (await first.get_approval("a2")).runtime_ref == {"thread": "lc-v2:x"}
    pending = await second.list_approvals(conversation_id=conversation.id, status="pending")
    assert [item.id for item in pending] == ["a2"]

    await second.cancel_pending_approvals(conversation.id)

    statuses = {item.id: item.status for item in await first.list_approvals(user_id="u1", tenant_id="t1")}
    assert statuses == {"a1": "approved", "a2": "denied", "a3": "pending"}
    with pytest.raises(PlatformError):
        await first.set_approval_status("missing", "approved")


async def test_interrupted_runs_are_listed_across_users() -> None:
    from datetime import timedelta

    from tianzhou_agent_platform.store.memory_repository import INTERRUPTED_RUN_ERROR, finished_run, started_run

    first, _second = _replicas()
    store = first._conversation_store
    running, interrupted, failed, idle = [
        await first.create_conversation(ConversationCreate(user_id=f"u{index}", tenant_id="t1"))
        for index in range(4)
    ]
    await store.mutate(running.id, lambda conversation: started_run(conversation, "tr1"))
    await store.mutate(
        interrupted.id, lambda conversation: finished_run(conversation, status="failed", error=INTERRUPTED_RUN_ERROR)
    )
    await store.mutate(failed.id, lambda conversation: finished_run(conversation, status="failed", error="boom"))
    since = datetime.now(UTC) - timedelta(minutes=5)

    listed = await store.list_with_run_status(running=True, failed_with=INTERRUPTED_RUN_ERROR, since=since)
    only_failed = await store.list_with_run_status(running=False, failed_with=INTERRUPTED_RUN_ERROR, since=since)
    later = await store.list_with_run_status(
        running=True, failed_with=INTERRUPTED_RUN_ERROR, since=datetime.now(UTC) + timedelta(minutes=5)
    )

    assert sorted(item.id for item in listed) == sorted([running.id, interrupted.id])
    assert [item.id for item in only_failed] == [interrupted.id]
    assert later == [] and idle.id not in {item.id for item in listed}
