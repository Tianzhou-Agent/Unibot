"""Architecture and service-boundary checks for the LangChain migration.

Enforce Section 7.2 dependency rules for migrated packages. These are
migration gates: runtime isolation, protocol injection and composition smoke.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "tianzhou_agent_platform"

AGENT_CORE = SRC.parent / "unibot_agent"

# Everything the agent core may import besides the standard library and itself.
AGENT_CORE_ALLOWED = ("pydantic", "langchain", "langchain_core", "langgraph", "unibot_agent")

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


def test_agent_core_imports_only_the_standard_library_pydantic_and_langchain() -> None:
    assert AGENT_CORE.is_dir()
    offenders: list[str] = []
    for path in AGENT_CORE.rglob("*.py"):
        for name in _imported_names(path):
            root = name.split(".")[0]
            if name.startswith("relative:") or root in sys.stdlib_module_names or root in AGENT_CORE_ALLOWED:
                continue
            offenders.append(f"{path.relative_to(AGENT_CORE)}: {name}")
    assert not offenders, f"unibot_agent must not import: {offenders}"


def test_agent_core_loads_without_the_platform() -> None:
    loaded = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, unibot_agent.agent, unibot_agent.ports; "
            "print([name for name in sys.modules if name.split('.')[0] == 'tianzhou_agent_platform'])",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert loaded.stdout.strip() == "[]"


def test_only_the_chat_workflow_and_the_composition_root_drive_the_agent() -> None:
    importers = sorted(
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if "unibot_agent.agent" in _imported_names(path)
    )
    assert importers == ["main.py", "services/chat.py"]


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

    from unibot_agent.runtime import build_agent
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
    # Composed protocols inherit methods from the feature protocols they combine.
    names: dict[str, None] = {}
    for cls in protocol.__mro__:
        names.update((name, None) for name, value in vars(cls).items() if inspect.iscoroutinefunction(value))
    return list(names)


@pytest.mark.parametrize(
    "protocol_path",
    [
        "tianzhou_agent_platform.conversations.repository:ConversationRepository",
        "tianzhou_agent_platform.conversations.repository:ApprovalRepository",
        "tianzhou_agent_platform.conversations.repository:ConversationRunRepository",
        "tianzhou_agent_platform.model_providers.repository:ModelProviderRepository",
        "tianzhou_agent_platform.aina.package.service:PackageRegistry",
        "tianzhou_agent_platform.aina.package.tasks:AinaTaskRepository",
        "tianzhou_agent_platform.services.agent_integration.capabilities:CapabilityRegistry",
        "tianzhou_agent_platform.sandbox.service:SandboxStore",
        "tianzhou_agent_platform.aina.project_service:AinaProjectRepository",
        "tianzhou_agent_platform.aina.managed:ManagedAinaStore",
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
    # Every route reaches storage through a feature service or a typed accessor of api/dependencies.py.
    offenders: list[str] = []
    for path in sorted((SRC / "api").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "tianzhou_agent_platform.api.dependencies":
                offenders.extend(path.name for alias in node.names if alias.name == "repository")
    assert sorted(set(offenders)) == sorted(ROUTES_PENDING_FEATURE_ACCESSORS)


# Phase 2 ratchets: modules not yet moved onto feature protocols. Each migration step removes its modules from
# these sets; a module that starts using the aggregate repository again fails the tests below.
ROUTES_PENDING_FEATURE_ACCESSORS = {
    "aina_add_app.py",
    "aina_host.py",
    "aina_host_services.py",
    "aina_ui.py",
    "capabilities.py",
    "developer_tokens.py",
    "documents.py",
    "feedback.py",
    "memories.py",
    "notifications.py",
    "operations.py",
    "schedules.py",
    "workspaces.py",
}

PENDING_AGGREGATE_IMPORTERS = {
    "aina/builtin.py",
    "aina/document/task_service.py",
    "aina/memory/builtin.py",
    "aina/package/tasks.py",
    "aina/scheduler.py",
    "api/aina_host.py",
    "api/capabilities.py",
    "api/dependencies.py",
    "api/schedules.py",
    "auth/oidc.py",
    "auth/service.py",
    "observability/service.py",
    "services/platform_tools.py",
    "tasks/service.py",
}

# Behaviour-sensitive (plan step 14): these reach Redis or scheduler claims through the repository object.
PENDING_PRIVATE_STATE_READERS = {
    "aina/document/task_service.py",
    "aina/scheduler.py",
    "sandbox/service.py",
}

# Names under which callers hold the aggregate repository (or a protocol-typed view of it).
REPOSITORY_RECEIVERS = frozenset(
    {
        "repository",
        "repo",
        "data_repository",
        "repository(request)",
        "self.repository",
        "self._repository",
        "self._repo",
        "self.registry",
        "self.service.repository",
        "self.aina_tasks.repository",
    }
)

# Migrated modules and the protocols their repository receivers are typed with.
PROTOCOL_CONSUMERS: dict[str, tuple[str, ...]] = {
    "conversations/service.py": (
        "tianzhou_agent_platform.conversations.repository:ConversationRepository",
        "tianzhou_agent_platform.conversations.repository:ApprovalRepository",
        "tianzhou_agent_platform.conversations.repository:ConversationRunRepository",
    ),
    "conversations/attachment_service.py": ("tianzhou_agent_platform.conversations.repository:ConversationRepository",),
    "model_providers/service.py": ("tianzhou_agent_platform.model_providers.repository:ModelProviderRepository",),
    "aina/package/service.py": ("tianzhou_agent_platform.aina.package.service:PackageRegistry",),
    "aina/package/tasks.py": ("tianzhou_agent_platform.aina.package.tasks:AinaTaskRepository",),
    "services/agent_integration/capabilities.py": (
        "tianzhou_agent_platform.services.agent_integration.capabilities:CapabilityRegistry",
    ),
    "services/agent_integration/prompts.py": (
        "tianzhou_agent_platform.services.agent_integration.capabilities:CapabilityRegistry",
    ),
    "services/agent_integration/provider.py": (
        "tianzhou_agent_platform.services.agent_integration.capabilities:CapabilityRegistry",
    ),
    "sandbox/service.py": ("tianzhou_agent_platform.sandbox.service:SandboxStore",),
    "aina/project_service.py": ("tianzhou_agent_platform.aina.project_service:AinaProjectRepository",),
    "aina/managed.py": ("tianzhou_agent_platform.aina.managed:ManagedAinaStore",),
}

PENDING_PROTOCOL_CONSUMERS = {
    "aina/builtin.py",
    "aina/document/task_service.py",
    "aina/memory/builtin.py",
    "aina/package/bundled.py",
    "aina/package/devhost.py",
    "aina/scheduler.py",
    "api/aina_add_app.py",
    "api/aina_host.py",
    "api/aina_host_services.py",
    "api/aina_ui.py",
    "api/capabilities.py",
    "api/developer_tokens.py",
    "api/documents.py",
    "api/feedback.py",
    "api/memories.py",
    "api/notifications.py",
    "api/operations.py",
    "api/schedules.py",
    "api/workspaces.py",
    "auth/oidc.py",
    "auth/service.py",
    "core/capability_discovery.py",
    "observability/service.py",
    "services/agent_integration/adapters.py",
    "services/platform_tools.py",
    "tasks/service.py",
}

# Feature modules store/ may import: data models, errors and the few helpers it persists with. Adding a service,
# route or composition import to store/ fails; removing one only needs this set shrunk.
STORE_ALLOWED_FEATURE_IMPORTS = {
    "tianzhou_agent_platform.aina.document.task_models",
    "tianzhou_agent_platform.aina.memory.models",
    "tianzhou_agent_platform.aina.package.files",
    "tianzhou_agent_platform.aina.package.notifications",
    "tianzhou_agent_platform.aina.package.storage",
    "tianzhou_agent_platform.aina.package.tasks",
    "tianzhou_agent_platform.aina.project",
    "tianzhou_agent_platform.aina.protocol.models",
    "tianzhou_agent_platform.aina.scheduler",
    "tianzhou_agent_platform.aina.skill.models",
    "tianzhou_agent_platform.aina.tool.models",
    "tianzhou_agent_platform.auth.models",
    "tianzhou_agent_platform.conversations.attachments",
    "tianzhou_agent_platform.conversations.models",
    "tianzhou_agent_platform.conversations.schemas",
    "tianzhou_agent_platform.core.errors",
    "tianzhou_agent_platform.core.feedback",
    "tianzhou_agent_platform.core.workspace",
    "tianzhou_agent_platform.model_providers.models",
    "tianzhou_agent_platform.observability.context",
    "tianzhou_agent_platform.observability.models",
    "tianzhou_agent_platform.observability.trace_details",
    "tianzhou_agent_platform.sandbox.models",
    "tianzhou_agent_platform.tasks.store",
}

_AGGREGATE_MODULES = ("tianzhou_agent_platform.store.memory_repository", "tianzhou_agent_platform.store.repository")


def _source_modules() -> list[tuple[str, ast.Module]]:
    return [
        (str(path.relative_to(SRC)), ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(SRC.rglob("*.py"))
        if not str(path.relative_to(SRC)).startswith("store/")
    ]


def _aggregate_methods() -> set[str]:
    from tianzhou_agent_platform.store.memory_repository import InMemoryRepository

    return {
        name
        for name, value in vars(InMemoryRepository).items()
        if not name.startswith("_") and inspect.iscoroutinefunction(value)
    }


def _repository_reads(tree: ast.Module) -> set[str]:
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and ast.unparse(node.value) in REPOSITORY_RECEIVERS
    }


def test_only_the_composition_root_imports_the_aggregate_repository() -> None:
    importers: set[str] = set()
    for module, tree in _source_modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in _AGGREGATE_MODULES:
                importers.add(module)
            elif isinstance(node, ast.ImportFrom) and node.module == "tianzhou_agent_platform.store":
                if any(alias.name == "PersistentRepository" for alias in node.names):
                    importers.add(module)
            elif isinstance(node, ast.Import) and any(alias.name in _AGGREGATE_MODULES for alias in node.names):
                importers.add(module)
    assert sorted(importers - {"main.py"}) == sorted(PENDING_AGGREGATE_IMPORTERS)


def test_callers_use_only_their_protocol_methods() -> None:
    import importlib

    aggregate = _aggregate_methods()
    unmapped: list[str] = []
    widened: list[str] = []
    for module, tree in _source_modules():
        if module == "main.py":
            continue
        reads = _repository_reads(tree)
        if module in PROTOCOL_CONSUMERS:
            allowed: set[str] = set()
            for protocol_path in PROTOCOL_CONSUMERS[module]:
                module_name, _, class_name = protocol_path.partition(":")
                allowed.update(_protocol_methods(getattr(importlib.import_module(module_name), class_name)))
            widened.extend(f"{module}: {name}" for name in sorted((reads & aggregate) - allowed))
        elif reads & aggregate and module not in PENDING_PROTOCOL_CONSUMERS:
            unmapped.append(f"{module}: {sorted(reads & aggregate)}")
    assert not widened, f"methods outside the declared protocols: {widened}"
    assert not unmapped, f"modules using the repository without a declared protocol: {unmapped}"
    assert not PENDING_PROTOCOL_CONSUMERS & set(PROTOCOL_CONSUMERS)
    still_pending = {module for module, tree in _source_modules() if _repository_reads(tree) & aggregate}
    assert PENDING_PROTOCOL_CONSUMERS <= still_pending, sorted(PENDING_PROTOCOL_CONSUMERS - still_pending)


def test_no_caller_reads_repository_private_state() -> None:
    readers: set[str] = set()
    for module, tree in _source_modules():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr.startswith("_")
                and not node.attr.startswith("__")
                and ast.unparse(node.value) in REPOSITORY_RECEIVERS
            ):
                readers.add(module)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"getattr", "hasattr"}
                and node.args
                and ast.unparse(node.args[0]) in REPOSITORY_RECEIVERS
            ):
                readers.add(module)
    assert sorted(readers) == sorted(PENDING_PRIVATE_STATE_READERS)


def test_store_imports_only_feature_models() -> None:
    imported: set[str] = set()
    for path in (SRC / "store").rglob("*.py"):
        for name in _imported_names(path):
            if name.startswith("tianzhou_agent_platform.") and not name.startswith("tianzhou_agent_platform.store"):
                imported.add(name)
    assert imported <= STORE_ALLOWED_FEATURE_IMPORTS, sorted(imported - STORE_ALLOWED_FEATURE_IMPORTS)


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
