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
