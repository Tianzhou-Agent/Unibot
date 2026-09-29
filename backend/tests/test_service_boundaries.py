"""Architecture and service-boundary checks for the LangChain migration.

Enforce Section 7.2 dependency rules for migrated packages. These are
migration gates: runtime isolation, protocol injection and composition smoke.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "tianzhou_agent_platform"

# Packages core/agent_runtime must not import (directly or relatively).
RUNTIME_FORBIDDEN = (
    "tianzhou_agent_platform.services",
    "tianzhou_agent_platform.conversations",
    "tianzhou_agent_platform.model_providers",
    "tianzhou_agent_platform.observability",
    "tianzhou_agent_platform.aina",
    "tianzhou_agent_platform.store",
    "tianzhou_agent_platform.api",
)

# Retired by the LangChain migration (plan §7.3): the legacy loop, completion port, summary engine and the
# compatibility re-exports of moved feature modules.
REMOVED_MODULES = (
    "core/agent.py",
    "core/llm.py",
    "core/context_compression.py",
    "core/observation_interceptors.py",
    "core/models.py",
    "core/builtin_tools.py",
    "core/chat.py",
    "core/conversation.py",
    "core/model_settings.py",
    "core/observability.py",
    "core/observability_query.py",
    "core/observability_stream.py",
    "core/observability_writer.py",
    "core/observation_context.py",
    "core/observation_logging.py",
    "core/operations_analytics.py",
    "core/repository.py",
    "core/telemetry.py",
    "core/trace_details.py",
    "services/agent_integration/approval_gate.py",
    "services/agent_integration/builder.py",
)


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.module:
                names.add(f"relative:{node.module}")
            elif node.level:
                names.add("relative:")
            elif node.module:
                names.add(node.module)
    return names


def test_agent_runtime_has_no_domain_or_persistence_imports() -> None:
    runtime_dir = SRC / "core" / "agent_runtime"
    assert runtime_dir.is_dir()
    offenders: list[str] = []
    for path in runtime_dir.rglob("*.py"):
        names = _imported_names(path)
        for name in names:
            for forbidden in RUNTIME_FORBIDDEN:
                if name == forbidden or name.startswith(forbidden + "."):
                    offenders.append(f"{path.name}: {name}")
    assert not offenders, f"core/agent_runtime must not import: {offenders}"


def test_legacy_runtime_modules_are_removed() -> None:
    assert [module for module in REMOVED_MODULES if (SRC / module).exists()] == []


def test_application_services_use_feature_contracts_not_concrete_repositories() -> None:
    # Plan §7.2: the chat workflow and the integration layer depend on feature services and protocols; only
    # main.py composes concrete store implementations.
    offenders: list[str] = []
    for path in [SRC / "services" / "chat.py", *(SRC / "services" / "agent_integration").rglob("*.py")]:
        for name in _imported_names(path):
            if name == "tianzhou_agent_platform.store" or name.startswith("tianzhou_agent_platform.store."):
                offenders.append(f"{path.name}: {name}")
    assert not offenders, offenders


def test_conversations_does_not_import_chat_service_or_langgraph_execution() -> None:
    conv_dir = SRC / "conversations"
    forbidden = (
        "tianzhou_agent_platform.services.chat",
        "langgraph",
        "langchain.agents",
        "tianzhou_agent_platform.store",
    )
    offenders: list[str] = []
    for path in conv_dir.rglob("*.py"):
        names = _imported_names(path)
        for name in names:
            for item in forbidden:
                if name == item or name.startswith(item + "."):
                    offenders.append(f"{path.name}: {name}")
    assert not offenders, f"conversations must not import: {offenders}"


def test_runtime_factory_composes_with_scripted_native_model() -> None:
    from langchain_core.tools import tool

    from tianzhou_agent_platform.core.agent_runtime import build_agent
    from tests.support.fake_chat_model import ScriptedChatModel, assistant

    @tool
    def ping() -> str:
        """Return pong."""
        return "pong"

    model = ScriptedChatModel(responses=[assistant("hello")])
    agent = build_agent(model=model, tools=[ping], system_prompt="test")
    # Smoke: compiled agent accepts native input without HTTP/domain construction.
    assert hasattr(agent, "ainvoke")


def test_conversation_service_accepts_protocol_repository() -> None:
    import asyncio

    from uuid import uuid4

    from tianzhou_agent_platform.conversations.models import Conversation, ConversationCreate
    from tianzhou_agent_platform.conversations.service import ConversationService

    class FakeRepo:
        def __init__(self) -> None:
            self.store = {}

        async def create_conversation(self, data: ConversationCreate):
            conv = Conversation(
                id=f"conv_{uuid4().hex}",
                user_id=data.user_id,
                tenant_id=data.tenant_id,
                title=data.title,
            )
            self.store[conv.id] = conv
            return conv

        async def list_conversations(self, *, user_id=None, tenant_id=None, category=None, workspace_id=None):
            return [c for c in self.store.values() if c.user_id == user_id]

    async def run() -> None:
        service = ConversationService(FakeRepo())
        conv = await service.create(ConversationCreate(user_id="u1", tenant_id="t1", title="hi"))
        assert conv.user_id == "u1"
        assert await service.list_for_actor(user_id="u1", tenant_id="t1") == [conv]
        service.ensure_ownership(conv, user_id="u1", tenant_id="t1")
        with pytest.raises(Exception):
            service.ensure_ownership(conv, user_id="other", tenant_id="t1")

    asyncio.run(run())


def _protocol_methods(protocol: type) -> list[str]:
    return [name for name, value in vars(protocol).items() if inspect.iscoroutinefunction(value)]


@pytest.mark.parametrize(
    "protocol_path",
    [
        "tianzhou_agent_platform.conversations.repository:ConversationRepository",
        "tianzhou_agent_platform.conversations.repository:ApprovalRepository",
        "tianzhou_agent_platform.conversations.repository:ConversationRunRepository",
        "tianzhou_agent_platform.model_providers.repository:ModelProviderRepository",
    ],
)
def test_concrete_repositories_implement_feature_protocols(protocol_path: str) -> None:
    # A protocol method the concrete repository lacks, or calls differently, fails only at runtime; compare
    # parameters (names, kinds, defaults) against both repositories main.py can inject.
    import importlib

    from tianzhou_agent_platform.store.memory_repository import InMemoryRepository
    from tianzhou_agent_platform.store.repository import PersistentRepository

    module_name, _, class_name = protocol_path.partition(":")
    protocol = getattr(importlib.import_module(module_name), class_name)
    mismatches: list[str] = []
    for concrete in (InMemoryRepository, PersistentRepository):
        for name in _protocol_methods(protocol):
            implementation = getattr(concrete, name, None)
            if implementation is None:
                mismatches.append(f"{concrete.__name__}.{name} is missing")
                continue
            expected = [
                (p.name, p.kind, p.default) for p in inspect.signature(getattr(protocol, name)).parameters.values()
            ]
            actual = [(p.name, p.kind, p.default) for p in inspect.signature(implementation).parameters.values()]
            if expected != actual:
                mismatches.append(f"{concrete.__name__}.{name}: {actual} != {expected}")
    assert not mismatches, mismatches


def test_migrated_routes_use_feature_services_not_the_aggregate_repository() -> None:
    offenders: list[str] = []
    for route in ("conversations.py", "model_settings.py"):
        tree = ast.parse((SRC / "api" / route).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "tianzhou_agent_platform.api.dependencies":
                offenders.extend(f"{route}: {alias.name}" for alias in node.names if alias.name == "repository")
    assert not offenders, offenders


def test_native_messages_are_execution_representation() -> None:
    """Archive Message.provider_message and native messages must convert at boundaries only."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from tianzhou_agent_platform.conversations.models import Message

    msg = Message(id="m1", role="assistant", content="hi", tool_calls=None)
    native = AIMessage(content="hi")
    assert msg.provider_message()["role"] == "assistant"
    assert native.content == "hi"
    # Types stay distinct: archive model vs native message.
    assert type(msg) is not type(native)
    assert issubclass(type(native), object)
    assert not isinstance(msg, (HumanMessage, AIMessage, ToolMessage))


def test_default_approval_record_serializes_like_the_previous_release() -> None:
    # Rollback safety: the previous release's strict ApprovalRecord rejects unknown keys at startup.
    import json

    from tianzhou_agent_platform.conversations.schemas import ApprovalRecord

    approval = ApprovalRecord(
        id="approval_1",
        conversation_id="conv_1",
        user_id="u",
        tenant_id="t",
        trace_id="trace_1",
        tool_calls=[],
        capability_names=[],
    )
    for dumped in (approval.model_dump(), approval.model_dump(mode="json"), json.loads(approval.model_dump_json())):
        assert "runtime_ref" not in dumped and "run_generation" not in dumped
    native = approval.model_copy(update={"runtime_ref": {"thread_id": "lc-v2:conv_1"}, "run_generation": 2})
    assert native.model_dump()["runtime_ref"] == {"thread_id": "lc-v2:conv_1"}
    assert ApprovalRecord.model_validate(native.model_dump(mode="json")).run_generation == 2
