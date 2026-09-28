from __future__ import annotations

import base64
import builtins
import hashlib
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.base import SerializerProtocol
from sqlalchemy import JSON, Column, DateTime, Index, MetaData, String, Table

from tianzhou_agent_platform.store.models import DeleteResult, StorePage, StoreQuery, StoreRecord

GRAPH_CHECKPOINTS_RESOURCE = "graph_checkpoints"
GRAPH_CHECKPOINT_WRITES_RESOURCE = "graph_checkpoint_writes"

graph_checkpoint_metadata = MetaData()
graph_checkpoints_table = Table(
    "unibot_graph_checkpoints",
    graph_checkpoint_metadata,
    Column("id", String(64), primary_key=True),
    Column("thread_id", String(64), nullable=False),
    Column("checkpoint_ns", String(255), nullable=False),
    Column("checkpoint_id", String(128), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_unibot_graph_checkpoint_thread", "thread_id", "checkpoint_ns"),
)
graph_checkpoint_writes_table = Table(
    "unibot_graph_checkpoint_writes",
    graph_checkpoint_metadata,
    Column("id", String(64), primary_key=True),
    Column("thread_id", String(64), nullable=False),
    Column("checkpoint_ns", String(255), nullable=False),
    Column("checkpoint_id", String(128), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_unibot_graph_write_checkpoint", "thread_id", "checkpoint_ns", "checkpoint_id"),
)
graph_checkpoint_tables = {
    GRAPH_CHECKPOINTS_RESOURCE: graph_checkpoints_table,
    GRAPH_CHECKPOINT_WRITES_RESOURCE: graph_checkpoint_writes_table,
}


class CheckpointDatabase(Protocol):
    async def create_tables(self, metadata: MetaData) -> None: ...

    async def create(self, resource: str, values: dict[str, Any]) -> StoreRecord: ...

    async def read(self, resource: str, record_id: str | int) -> StoreRecord | None: ...

    async def update(self, resource: str, record_id: str | int, values: dict[str, Any]) -> StoreRecord: ...

    async def delete(self, resource: str, record_id: str | int) -> DeleteResult: ...

    async def query(self, resource: str, query: StoreQuery) -> StorePage: ...


class MySqlCheckpointSaver(BaseCheckpointSaver[str]):
    """Async LangGraph checkpointer backed by the platform MySQL store."""

    def __init__(self, database: CheckpointDatabase, *, serde: SerializerProtocol | None = None) -> None:
        super().__init__(serde=serde)
        self.database = database

    async def initialize(self) -> None:
        await self.database.create_tables(graph_checkpoint_metadata)

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        raise NotImplementedError("MySqlCheckpointSaver only supports async graph execution")

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        raise NotImplementedError("MySqlCheckpointSaver only supports async graph execution")

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        raise NotImplementedError("MySqlCheckpointSaver only supports async graph execution")

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        raise NotImplementedError("MySqlCheckpointSaver only supports async graph execution")

    def delete_thread(self, thread_id: str) -> None:
        raise NotImplementedError("MySqlCheckpointSaver only supports async graph execution")

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id, checkpoint_ns = _thread_scope(config)
        checkpoint_id = checkpoint["id"]
        record_id = _record_id("checkpoint", thread_id, checkpoint_ns, checkpoint_id)
        values = {
            "thread_id": thread_id,
            "checkpoint_ns": checkpoint_ns,
            "checkpoint_id": checkpoint_id,
            "payload": {
                "checkpoint": self._dump(checkpoint),
                "metadata": self._dump(get_checkpoint_metadata(config, metadata)),
                "parent_checkpoint_id": config["configurable"].get("checkpoint_id"),
            },
            "updated_at": datetime.now(UTC),
        }
        await self._upsert(GRAPH_CHECKPOINTS_RESOURCE, record_id, values)
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id, checkpoint_ns = _thread_scope(config)
        checkpoint_id = str(config["configurable"]["checkpoint_id"])
        for position, (channel, value) in enumerate(writes):
            index = WRITES_IDX_MAP.get(channel, position)
            record_id = _record_id("write", thread_id, checkpoint_ns, checkpoint_id, task_id, str(index))
            existing = await self.database.read(GRAPH_CHECKPOINT_WRITES_RESOURCE, record_id)
            if existing is not None and index >= 0:
                continue
            values = {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
                "payload": {
                    "task_id": task_id,
                    "task_path": task_path,
                    "index": index,
                    "channel": channel,
                    "value": self._dump(value),
                },
                "updated_at": datetime.now(UTC),
            }
            await self._upsert(GRAPH_CHECKPOINT_WRITES_RESOURCE, record_id, values)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id, checkpoint_ns = _thread_scope(config)
        checkpoint_id = get_checkpoint_id(config)
        record: StoreRecord | None
        if checkpoint_id is not None:
            record = await self.database.read(
                GRAPH_CHECKPOINTS_RESOURCE,
                _record_id("checkpoint", thread_id, checkpoint_ns, checkpoint_id),
            )
        else:
            records = await self._query_all(
                GRAPH_CHECKPOINTS_RESOURCE,
                {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns},
            )
            record = max(records, key=lambda item: str(item.values["checkpoint_id"]), default=None)
        if record is None:
            return None
        return await self._tuple_from_record(record)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        query_filters: dict[str, Any] = {}
        checkpoint_ns: str | None = None
        checkpoint_id: str | None = None
        if config is not None:
            thread_id, checkpoint_ns = _thread_scope(config)
            query_filters["thread_id"] = thread_id
            if "checkpoint_ns" in config["configurable"]:
                query_filters["checkpoint_ns"] = checkpoint_ns
            checkpoint_id = get_checkpoint_id(config)
        records = await self._query_all(GRAPH_CHECKPOINTS_RESOURCE, query_filters)
        before_id = get_checkpoint_id(before) if before is not None else None
        emitted = 0
        for record in sorted(records, key=lambda item: str(item.values["checkpoint_id"]), reverse=True):
            current_id = str(record.values["checkpoint_id"])
            if checkpoint_id is not None and current_id != checkpoint_id:
                continue
            if before_id is not None and current_id >= before_id:
                continue
            checkpoint_tuple = await self._tuple_from_record(record)
            if filter and not all(checkpoint_tuple.metadata.get(key) == value for key, value in filter.items()):
                continue
            yield checkpoint_tuple
            emitted += 1
            if limit is not None and emitted >= limit:
                return

    async def adelete_thread(self, thread_id: str) -> None:
        for resource in (GRAPH_CHECKPOINT_WRITES_RESOURCE, GRAPH_CHECKPOINTS_RESOURCE):
            records = await self._query_all(resource, {"thread_id": thread_id})
            for record in records:
                await self.database.delete(resource, record.id)

    async def _tuple_from_record(self, record: StoreRecord) -> CheckpointTuple:
        thread_id = str(record.values["thread_id"])
        checkpoint_ns = str(record.values["checkpoint_ns"])
        checkpoint_id = str(record.values["checkpoint_id"])
        payload = cast(dict[str, Any], record.values["payload"])
        writes = await self._query_all(
            GRAPH_CHECKPOINT_WRITES_RESOURCE,
            {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            },
        )
        pending_writes = []
        for write in sorted(writes, key=lambda item: int(item.values["payload"]["index"])):
            write_payload = cast(dict[str, Any], write.values["payload"])
            pending_writes.append(
                (
                    str(write_payload["task_id"]),
                    str(write_payload["channel"]),
                    self._load(cast(dict[str, str], write_payload["value"])),
                )
            )
        parent_checkpoint_id = payload.get("parent_checkpoint_id")
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                }
            },
            checkpoint=cast(Checkpoint, self._load(cast(dict[str, str], payload["checkpoint"]))),
            metadata=cast(CheckpointMetadata, self._load(cast(dict[str, str], payload["metadata"]))),
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": parent_checkpoint_id,
                    }
                }
                if parent_checkpoint_id
                else None
            ),
            pending_writes=pending_writes,
        )

    async def _query_all(
        self,
        resource: str,
        filters: dict[str, Any],
    ) -> builtins.list[StoreRecord]:
        records: builtins.list[StoreRecord] = []
        offset = 0
        while True:
            page = await self.database.query(resource, StoreQuery(filters=filters, limit=1000, offset=offset))
            records.extend(page.items)
            if len(page.items) < page.limit:
                return records
            offset += page.limit

    async def _upsert(self, resource: str, record_id: str, values: dict[str, Any]) -> None:
        if await self.database.read(resource, record_id) is None:
            await self.database.create(resource, {"id": record_id, **values})
        else:
            await self.database.update(resource, record_id, values)

    def _dump(self, value: Any) -> dict[str, str]:
        value_type, data = self.serde.dumps_typed(value)
        return {"type": value_type, "data": base64.b64encode(data).decode("ascii")}

    def _load(self, payload: dict[str, str]) -> Any:
        return self.serde.loads_typed((payload["type"], base64.b64decode(payload["data"])))


def _thread_scope(config: RunnableConfig) -> tuple[str, str]:
    configurable = config.get("configurable") or {}
    thread_id = configurable.get("thread_id")
    if not thread_id:
        raise ValueError("LangGraph checkpoint config requires configurable.thread_id")
    return str(thread_id), str(configurable.get("checkpoint_ns") or "")


def _record_id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
