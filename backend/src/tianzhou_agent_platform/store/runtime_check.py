from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import Column, Integer, MetaData, String, Table

from tianzhou_agent_platform.store.lifecycle import StorageStores
from tianzhou_agent_platform.store.models import StoragePath, StoreQuery

RUNTIME_CHECK_RESOURCE = "runtime_probe"

runtime_check_metadata = MetaData()
runtime_check_table = Table(
    "storage_runtime_probe",
    runtime_check_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(100), nullable=False),
    Column("status", String(20), nullable=False),
)


class StoreRuntimeCheckResult(BaseModel):
    mysql: bool
    redis: bool
    nas: bool
    mysql_record_id: int | str
    redis_key: str
    nas_path: str


async def run_storage_runtime_check(stores: StorageStores, nas_scope: str = "runtime") -> StoreRuntimeCheckResult:
    await stores.mysql.create_tables(runtime_check_metadata)

    suffix = uuid4().hex
    name = f"runtime-{suffix}"
    redis_key = f"runtime-{suffix}"
    nas_path = StoragePath(relative_path=str(Path(nas_scope) / f"{suffix}.txt").replace("\\", "/"))

    created = await stores.mysql.create(RUNTIME_CHECK_RESOURCE, {"name": name, "status": "new"})

    try:
        read = await stores.mysql.read(RUNTIME_CHECK_RESOURCE, created.id)
        if read is None or read.values["status"] != "new":
            raise RuntimeError("MySQL runtime check read failed")

        updated = await stores.mysql.update(RUNTIME_CHECK_RESOURCE, created.id, {"status": "done"})
        if updated.values["status"] != "done":
            raise RuntimeError("MySQL runtime check update failed")

        page = await stores.mysql.query(RUNTIME_CHECK_RESOURCE, StoreQuery(filters={"status": "done"}))
        if created.id not in {item.id for item in page.items}:
            raise RuntimeError("MySQL runtime check query failed")

        await stores.redis.delete("runtime", redis_key)
        redis_written = await stores.redis.set("runtime", redis_key, {"mysql_id": created.id}, ttl_seconds=60)
        redis_entry = await stores.redis.get("runtime", redis_key)
        if not redis_written.written or redis_entry is None or redis_entry.value != {"mysql_id": created.id}:
            raise RuntimeError("Redis runtime check failed")

        nas_metadata = await stores.nas.write(nas_path, b"storage runtime check")
        nas_content = await stores.nas.read(nas_path)
        if nas_metadata.size_bytes != len(b"storage runtime check") or nas_content != b"storage runtime check":
            raise RuntimeError("NAS runtime check failed")

        return StoreRuntimeCheckResult(
            mysql=True,
            redis=True,
            nas=True,
            mysql_record_id=created.id,
            redis_key=redis_key,
            nas_path=nas_path.relative_path,
        )
    finally:
        await stores.nas.delete(nas_path)
        await stores.redis.delete("runtime", redis_key)
        await stores.mysql.delete(RUNTIME_CHECK_RESOURCE, created.id)
