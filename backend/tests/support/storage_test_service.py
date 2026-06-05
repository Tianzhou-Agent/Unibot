from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, cast

from fastapi import Body, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, text

from tianzhou_agent_platform.store.errors import StorageError, StorageErrorCode
from tianzhou_agent_platform.store.lifecycle import StorageStores, create_storage_stores
from tianzhou_agent_platform.store.models import (
    CacheEntry,
    DeleteResult,
    FileMetadata,
    StoragePath,
    StoreCondition,
    StorePage,
    StoreQuery,
    StoreRecord,
    WriteResult,
)
from tianzhou_agent_platform.store.runtime_check import (
    RUNTIME_CHECK_RESOURCE,
    StoreRuntimeCheckResult,
    run_storage_runtime_check,
    runtime_check_metadata,
    runtime_check_table,
)
from tianzhou_agent_platform.store.settings import StorageSettings

TEST_ITEM_RESOURCE = "test_items"

test_service_metadata = MetaData()
test_items_table = Table(
    "storage_test_items",
    test_service_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(100), nullable=False),
    Column("status", String(20), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


class MySqlItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    status: str = Field(default="new", min_length=1, max_length=20)


class MySqlItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    status: str | None = Field(default=None, min_length=1, max_length=20)


class RedisValueWrite(BaseModel):
    value: Any
    ttl_seconds: int | None = Field(default=None, gt=0)


class RedisExpireRequest(BaseModel):
    ttl_seconds: int = Field(gt=0)


def _status_for_error(code: StorageErrorCode) -> int:
    if code == StorageErrorCode.NOT_FOUND:
        return status.HTTP_404_NOT_FOUND
    if code in {StorageErrorCode.BACKEND_UNAVAILABLE, StorageErrorCode.TIMEOUT}:
        return status.HTTP_503_SERVICE_UNAVAILABLE
    if code == StorageErrorCode.UNSUPPORTED_CAPABILITY:
        return status.HTTP_501_NOT_IMPLEMENTED
    return status.HTTP_400_BAD_REQUEST


def _stores(request: Request) -> StorageStores:
    stores = getattr(request.app.state, "storage_stores", None)
    if stores is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Storage stores are not loaded")
    return cast(StorageStores, stores)


def _path(relative_path: str) -> StoragePath:
    try:
        return StoragePath(relative_path=relative_path)
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid NAS path") from exc


def _coerce_mysql_item_condition_value(field: str, value: str) -> Any:
    if field == "id":
        try:
            return int(value)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid MySQL id condition value") from exc
    if field == "created_at":
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid MySQL created_at condition value",
            ) from exc
    return value


def _conditions_from_query_params(
    fields: list[str],
    operators: list[str],
    values: list[str],
) -> list[StoreCondition]:
    if not fields and not operators and not values:
        return []
    if len(fields) != len(operators) or len(fields) != len(values):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="condition_field, condition_op, and condition_value must have the same count",
        )
    try:
        return [
            StoreCondition(
                field=field,
                op=operator,
                value=_coerce_mysql_item_condition_value(field.strip(), value),
            )
            for field, operator, value in zip(fields, operators, values, strict=True)
        ]
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid MySQL query condition") from exc


async def _ensure_test_items_created_at_column(stores: StorageStores) -> None:
    async with stores.mysql._engine.begin() as connection:
        result = await connection.execute(
            text(
                """
                SELECT COUNT(*)
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = 'storage_test_items'
                  AND column_name = 'created_at'
                """
            )
        )
        if result.scalar_one() == 0:
            await connection.execute(
                text(
                    """
                    ALTER TABLE storage_test_items
                    ADD COLUMN created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                    """
                )
            )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = StorageSettings()  # type: ignore[call-arg]
    stores = create_storage_stores(
        settings,
        mysql_resource_tables={
            TEST_ITEM_RESOURCE: test_items_table,
            RUNTIME_CHECK_RESOURCE: runtime_check_table,
        },
    )
    app.state.storage_stores = stores

    await stores.mysql.create_tables(test_service_metadata)
    await _ensure_test_items_created_at_column(stores)
    await stores.mysql.create_tables(runtime_check_metadata)

    try:
        yield
    finally:
        await stores.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Storage Test Service", lifespan=lifespan)

    @app.exception_handler(StorageError)
    async def storage_error_handler(_request: Request, exc: StorageError) -> JSONResponse:
        return JSONResponse(
            status_code=_status_for_error(exc.code),
            content={"detail": {"code": exc.code.value, "message": exc.message}},
        )

    @app.get("/health")
    async def health(request: Request) -> dict[str, bool | str]:
        return {"status": "ok", "storage_loaded": hasattr(request.app.state, "storage_stores")}

    @app.post("/storage/smoke-test", response_model=StoreRuntimeCheckResult)
    async def smoke_test(request: Request) -> StoreRuntimeCheckResult:
        return await run_storage_runtime_check(_stores(request), nas_scope="test-service")

    @app.post("/mysql/items", response_model=StoreRecord)
    async def create_mysql_item(request: Request, item: MySqlItemCreate) -> StoreRecord:
        values = item.model_dump()
        values["created_at"] = datetime.now(timezone.utc)
        return await _stores(request).mysql.create(TEST_ITEM_RESOURCE, values)

    @app.get("/mysql/items/{item_id}", response_model=StoreRecord)
    async def read_mysql_item(request: Request, item_id: int) -> StoreRecord:
        record = await _stores(request).mysql.read(TEST_ITEM_RESOURCE, item_id)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MySQL item was not found")
        return record

    @app.patch("/mysql/items/{item_id}", response_model=StoreRecord)
    async def update_mysql_item(request: Request, item_id: int, item: MySqlItemUpdate) -> StoreRecord:
        values = item.model_dump(exclude_unset=True, exclude_none=True)
        if not values:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No update values were provided")
        return await _stores(request).mysql.update(TEST_ITEM_RESOURCE, item_id, values)

    @app.delete("/mysql/items/{item_id}", response_model=DeleteResult)
    async def delete_mysql_item(request: Request, item_id: int) -> DeleteResult:
        return await _stores(request).mysql.delete(TEST_ITEM_RESOURCE, item_id)

    @app.get("/mysql/items", response_model=StorePage)
    async def query_mysql_items(
        request: Request,
        name: str | None = None,
        item_status: str | None = None,
        condition_field: list[str] = Query(default_factory=list),
        condition_op: list[str] = Query(default_factory=list),
        condition_value: list[str] = Query(default_factory=list),
        limit: int = 100,
        offset: int = 0,
    ) -> StorePage:
        filters = {}
        if name is not None:
            filters["name"] = name
        if item_status is not None:
            filters["status"] = item_status
        return await _stores(request).mysql.query(
            TEST_ITEM_RESOURCE,
            StoreQuery(
                filters=filters,
                conditions=_conditions_from_query_params(condition_field, condition_op, condition_value),
                limit=limit,
                offset=offset,
            ),
        )

    @app.put("/redis/{namespace}/{key}", response_model=WriteResult)
    async def set_redis_value(request: Request, namespace: str, key: str, body: RedisValueWrite) -> WriteResult:
        return await _stores(request).redis.set(namespace, key, body.value, ttl_seconds=body.ttl_seconds)

    @app.get("/redis/{namespace}/{key}", response_model=CacheEntry)
    async def get_redis_value(request: Request, namespace: str, key: str) -> CacheEntry:
        entry = await _stores(request).redis.get(namespace, key)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Redis key was not found")
        return entry

    @app.get("/redis/{namespace}/{key}/exists")
    async def redis_value_exists(request: Request, namespace: str, key: str) -> dict[str, bool]:
        return {"exists": await _stores(request).redis.exists(namespace, key)}

    @app.post("/redis/{namespace}/{key}/expire", response_model=WriteResult)
    async def expire_redis_value(request: Request, namespace: str, key: str, body: RedisExpireRequest) -> WriteResult:
        return await _stores(request).redis.expire(namespace, key, body.ttl_seconds)

    @app.delete("/redis/{namespace}/{key}", response_model=DeleteResult)
    async def delete_redis_value(request: Request, namespace: str, key: str) -> DeleteResult:
        return await _stores(request).redis.delete(namespace, key)

    @app.put("/nas/files/{relative_path:path}", response_model=FileMetadata)
    async def write_nas_file(
        request: Request,
        relative_path: str,
        content: bytes = Body(media_type="application/octet-stream"),
        overwrite: bool = True,
    ) -> FileMetadata:
        return await _stores(request).nas.write(
            _path(relative_path),
            content,
            overwrite=overwrite,
        )

    @app.get("/nas/files/{relative_path:path}/exists")
    async def nas_file_exists(request: Request, relative_path: str) -> dict[str, bool]:
        return {"exists": await _stores(request).nas.exists(_path(relative_path))}

    @app.get("/nas/files/{relative_path:path}")
    async def read_nas_file(request: Request, relative_path: str) -> Response:
        content = await _stores(request).nas.read(_path(relative_path))
        return Response(content=content, media_type="application/octet-stream")

    @app.get("/nas/metadata/{relative_path:path}", response_model=FileMetadata)
    async def get_nas_metadata(request: Request, relative_path: str) -> FileMetadata:
        return await _stores(request).nas.metadata(_path(relative_path))

    @app.delete("/nas/files/{relative_path:path}", response_model=DeleteResult)
    async def delete_nas_file(request: Request, relative_path: str) -> DeleteResult:
        return await _stores(request).nas.delete(_path(relative_path))

    return app


app = create_app()
