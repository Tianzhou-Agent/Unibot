from __future__ import annotations

from typing import Any, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from tianzhou_agent_platform.store.checkpoint import MySqlCheckpointSaver
from tianzhou_agent_platform.store.models import DeleteResult, StorePage, StoreQuery, StoreRecord


class FakeCheckpointDatabase:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, StoreRecord]] = {}
        self.initialized = False

    async def create_tables(self, metadata: Any) -> None:
        del metadata
        self.initialized = True

    async def create(self, resource: str, values: dict[str, Any]) -> StoreRecord:
        record_id = str(values["id"])
        record = StoreRecord(
            resource=resource,
            id=record_id,
            values={key: value for key, value in values.items() if key != "id"},
        )
        self.records.setdefault(resource, {})[record_id] = record
        return record

    async def read(self, resource: str, record_id: str | int) -> StoreRecord | None:
        return self.records.get(resource, {}).get(str(record_id))

    async def update(self, resource: str, record_id: str | int, values: dict[str, Any]) -> StoreRecord:
        current = self.records[resource][str(record_id)]
        updated = current.model_copy(update={"values": {**current.values, **values}}, deep=True)
        self.records[resource][str(record_id)] = updated
        return updated

    async def delete(self, resource: str, record_id: str | int) -> DeleteResult:
        deleted = self.records.get(resource, {}).pop(str(record_id), None) is not None
        return DeleteResult(deleted=deleted)

    async def query(self, resource: str, query: StoreQuery) -> StorePage:
        records = [
            record
            for record in self.records.get(resource, {}).values()
            if all(record.values.get(field) == value for field, value in query.filters.items())
        ]
        page = records[query.offset : query.offset + query.limit]
        return StorePage(items=page, limit=query.limit, offset=query.offset)


class CounterState(TypedDict):
    count: int


def _counter_graph(saver: MySqlCheckpointSaver):  # type: ignore[no-untyped-def]
    async def increment(state: CounterState) -> CounterState:
        return {"count": state["count"] + 1}

    graph = StateGraph(CounterState)
    graph.add_node("increment", increment)
    graph.add_edge(START, "increment")
    graph.add_edge("increment", END)
    return graph.compile(checkpointer=saver)


@pytest.mark.asyncio
async def test_mysql_checkpointer_restores_graph_state_after_recreation() -> None:
    database = FakeCheckpointDatabase()
    saver = MySqlCheckpointSaver(database)
    await saver.initialize()
    graph = _counter_graph(saver)
    config = {"configurable": {"thread_id": "trace-1"}}

    result = await graph.ainvoke({"count": 4}, config=config, durability="sync")
    assert result == {"count": 5}

    restored_graph = _counter_graph(MySqlCheckpointSaver(database))
    restored = await restored_graph.aget_state(config)
    history = [snapshot async for snapshot in restored_graph.aget_state_history(config)]

    assert database.initialized is True
    assert restored.values == {"count": 5}
    assert len(history) >= 2
    assert history[0].values == {"count": 5}


@pytest.mark.asyncio
async def test_mysql_checkpointer_isolates_trace_namespaces_and_deletes_thread() -> None:
    database = FakeCheckpointDatabase()
    saver = MySqlCheckpointSaver(database)
    graph = _counter_graph(saver)
    first = {"configurable": {"thread_id": "trace-a"}}
    second = {"configurable": {"thread_id": "trace-b"}}

    await graph.ainvoke({"count": 1}, config=first, durability="sync")
    await graph.ainvoke({"count": 10}, config=second, durability="sync")

    assert (await graph.aget_state(first)).values == {"count": 2}
    assert (await graph.aget_state(second)).values == {"count": 11}

    await saver.adelete_thread("trace-a")

    assert (await graph.aget_state(first)).values == {}
    assert (await graph.aget_state(second)).values == {"count": 11}


@pytest.mark.asyncio
async def test_native_approval_interrupt_resumes_after_saver_recreation() -> None:
    from langchain_core.messages import HumanMessage, ToolMessage
    from langchain_core.tools import tool
    from langgraph.types import Command

    from tianzhou_agent_platform.core.agent_runtime import build_agent
    from tianzhou_agent_platform.core.agent_runtime.middleware.approval_policy import TerminalDenialMiddleware
    from tianzhou_agent_platform.services.agent_integration.approvals import (
        CANCELLED_REPLY,
        DENIED_TOOL_RESULT,
        build_approval_middleware,
        resume_decisions,
    )
    from tianzhou_agent_platform.services.agent_integration.capabilities import Capability
    from tests.support.fake_chat_model import ScriptedChatModel, assistant, tool_calling

    sent: list[str] = []

    @tool
    def send(recipient: str) -> str:
        """Send a message."""
        sent.append(recipient)
        return "sent"

    registry = {
        "send": Capability(
            kind="builtin",
            capability_id="send",
            function_name="send",
            display_name="Send",
            description="",
            input_schema={"type": "object", "properties": {"recipient": {"type": "string"}}},
            requires_confirmation=True,
            value="send",
        )
    }

    def agent(saver: MySqlCheckpointSaver, *responses: Any):  # type: ignore[no-untyped-def]
        return build_agent(
            model=ScriptedChatModel(responses=list(responses)),
            tools=[send],
            middleware=[
                TerminalDenialMiddleware(tool_result=DENIED_TOOL_RESULT, reply=CANCELLED_REPLY),
                build_approval_middleware(registry),
            ],
            checkpointer=saver,
        )

    database = FakeCheckpointDatabase()
    config = {"configurable": {"thread_id": "lc-v2:conv_1"}}
    saver = MySqlCheckpointSaver(database)
    paused = agent(saver, tool_calling("send", {"recipient": "a@example.com"}))
    await paused.ainvoke({"messages": [HumanMessage(content="send it")]}, config=config)
    history_before = len(database.records["graph_checkpoints"])

    # Retention keeps only the latest checkpoint, with the pending interrupt write the resume needs.
    await saver.aprune(["lc-v2:conv_1"], strategy="keep_latest")
    assert history_before > 1
    assert len(database.records["graph_checkpoints"]) == 1

    # A new process: a new saver over the same storage restores the paused batch and its interrupt.
    resumed = agent(MySqlCheckpointSaver(database), assistant("Sent."))
    [interrupt] = (await resumed.aget_state(config)).interrupts
    assert sent == []
    result = await resumed.ainvoke(Command(resume=resume_decisions(interrupt, approve=True)), config=config)

    assert sent == ["a@example.com"]
    assert [message.content for message in result["messages"] if isinstance(message, ToolMessage)] == ["sent"]
    assert result["messages"][-1].content == "Sent."



@pytest.mark.asyncio
async def test_mysql_checkpointer_prune_keeps_latest_state_per_thread() -> None:
    database = FakeCheckpointDatabase()
    saver = MySqlCheckpointSaver(database)
    graph = _counter_graph(saver)
    first = {"configurable": {"thread_id": "t1"}}
    second = {"configurable": {"thread_id": "t2"}}
    for _ in range(3):
        await graph.ainvoke({"count": 1}, config=first, durability="sync")
    await graph.ainvoke({"count": 7}, config=second, durability="sync")
    second_history = len([snapshot async for snapshot in graph.aget_state_history(second)])

    await saver.aprune(["t1"], strategy="keep_latest")

    assert [snapshot.values async for snapshot in graph.aget_state_history(first)] == [{"count": 2}]
    assert len([snapshot async for snapshot in graph.aget_state_history(second)]) == second_history
    latest = (await graph.aget_state(first)).config["configurable"]["checkpoint_id"]
    assert all(
        record.values["thread_id"] == "t2" or record.values["checkpoint_id"] == latest
        for record in database.records["graph_checkpoint_writes"].values()
    )

    await saver.aprune(["t2"], strategy="delete")
    assert (await graph.aget_state(second)).values == {}
    with pytest.raises(ValueError, match="Unsupported"):
        await saver.aprune(["t1"], strategy="keep_some")



def test_agent_turns_keep_only_the_latest_checkpoint_and_continue_from_it() -> None:
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from tianzhou_agent_platform.config import AgentSettings
    from tianzhou_agent_platform.main import create_app
    from tests.support.fake_llm import ScriptedLLM, assistant

    database = FakeCheckpointDatabase()
    llm = ScriptedLLM([assistant("first answer"), assistant("second answer")])
    settings = AgentSettings(  # type: ignore[call-arg]
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key=SecretStr("test-key"),
        llm_model="test-model",
    )
    app = create_app(settings=settings, llm=llm)
    app.state.chat_service.runner.checkpointer = MySqlCheckpointSaver(database)
    with TestClient(app) as client:
        first = client.post("/chat", json={"message": "first question"}).json()
        client.post("/chat", json={"message": "follow up", "conversation_id": first["conversation_id"]})

    thread = f"lc-v2:{first['conversation_id']}"
    assert [record.values["thread_id"] for record in database.records["graph_checkpoints"].values()] == [thread]
    # The second turn continued from the pruned checkpoint's working memory.
    assert any(message.get("content") == "first answer" for message in llm.calls[1]["messages"])
