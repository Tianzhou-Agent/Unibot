from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


class _ApiException(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


class _KubernetesModel:
    def __init__(self, **values: object) -> None:
        for name, value in values.items():
            setattr(self, name, value)


def _load_operator(monkeypatch):
    def decorator(*_: object, **__: object):
        return lambda function: function

    class TemporaryError(Exception):
        def __init__(self, message: str, *, delay: int) -> None:
            super().__init__(message)
            self.delay = delay

    kopf = types.SimpleNamespace(
        on=types.SimpleNamespace(startup=decorator, create=decorator, update=decorator),
        timer=decorator,
        PermanentError=ValueError,
        TemporaryError=TemporaryError,
    )
    model_names = [
        "V1Capabilities",
        "V1Container",
        "V1ContainerPort",
        "V1EmptyDirVolumeSource",
        "V1EnvVar",
        "V1HTTPGetAction",
        "V1ObjectMeta",
        "V1OwnerReference",
        "V1PersistentVolumeClaim",
        "V1PersistentVolumeClaimSpec",
        "V1PersistentVolumeClaimVolumeSource",
        "V1Pod",
        "V1PodSecurityContext",
        "V1PodSpec",
        "V1Probe",
        "V1ResourceRequirements",
        "V1SeccompProfile",
        "V1SecurityContext",
        "V1Service",
        "V1ServicePort",
        "V1ServiceSpec",
        "V1Volume",
        "V1VolumeMount",
        "V1VolumeResourceRequirements",
    ]
    client = types.SimpleNamespace(
        CoreV1Api=lambda: None,
        **{name: _KubernetesModel for name in model_names},
    )
    config = types.SimpleNamespace(
        ConfigException=RuntimeError,
        load_incluster_config=lambda: None,
        load_kube_config=lambda: None,
    )
    kubernetes_asyncio = types.ModuleType("kubernetes_asyncio")
    kubernetes_asyncio.client = client
    kubernetes_asyncio.config = config
    kubernetes_client = types.ModuleType("kubernetes_asyncio.client")
    kubernetes_client.ApiException = _ApiException
    monkeypatch.setitem(sys.modules, "kopf", kopf)
    monkeypatch.setitem(sys.modules, "kubernetes_asyncio", kubernetes_asyncio)
    monkeypatch.setitem(sys.modules, "kubernetes_asyncio.client", kubernetes_client)
    operator_root = Path(__file__).resolve().parent
    monkeypatch.syspath_prepend(str(operator_root))
    spec = importlib.util.spec_from_file_location("test_sandbox_operator_main", operator_root / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _PodCore:
    def __init__(self) -> None:
        self.created: _KubernetesModel | None = None

    async def read_namespaced_pod(self, *_: object) -> object:
        raise _ApiException(404)

    async def create_namespaced_pod(self, _: str, body: _KubernetesModel) -> None:
        self.created = body


@pytest.mark.asyncio
async def test_workspace_pod_separates_runtime_and_shared_workspace(monkeypatch) -> None:
    operator = _load_operator(monkeypatch)
    plan = operator.sandbox_storage_plan(
        "unibot-workspace",
        {
            "workspaceId": "workspace_123",
            "workspaceStorageKey": "ws_123",
            "workspacePersistentVolumeClaim": "shared-workspaces",
        },
    )
    core = _PodCore()

    await operator.ensure_pod(
        core,
        "unibot-workspace",
        "unibot-sandboxes",
        {"image": "unibot/sandboxd:test"},
        [_KubernetesModel(name="owner")],
        plan,
    )

    assert core.created is not None
    pod = core.created
    volumes = {item.name: item for item in pod.spec.volumes}
    mounts = {item.name: item for item in pod.spec.containers[0].volume_mounts}
    environment = {item.name: item.value for item in pod.spec.containers[0].env}
    assert volumes["runtime"].persistent_volume_claim.claim_name == "unibot-workspace"
    assert volumes["workspace"].persistent_volume_claim.claim_name == "shared-workspaces"
    assert mounts["runtime"].mount_path == "/runtime"
    assert mounts["workspace"].mount_path == "/workspace"
    assert mounts["workspace"].sub_path == "workspaces/ws_123/files"
    assert environment["HOME"] == "/runtime"
    assert environment["SANDBOX_RUNTIME"] == "/runtime"
    init_container = pod.spec.init_containers[0]
    assert init_container.image == "unibot/sandboxd:test"
    assert init_container.security_context.privileged is False
    assert init_container.command == [
        "mkdir",
        "-p",
        "/shared-workspaces/workspaces/ws_123/files",
    ]


class _PvcCore:
    def __init__(self) -> None:
        self.read_names: list[str] = []
        self.created: list[_KubernetesModel] = []

    async def read_namespaced_persistent_volume_claim(self, name: str, _: str) -> object:
        self.read_names.append(name)
        if name == "unibot-workspace":
            raise _ApiException(404)
        return object()

    async def create_namespaced_persistent_volume_claim(
        self,
        _: str,
        body: _KubernetesModel,
    ) -> None:
        self.created.append(body)


@pytest.mark.asyncio
async def test_operator_owns_only_runtime_pvc_and_only_reads_shared_claim(monkeypatch) -> None:
    operator = _load_operator(monkeypatch)
    owner = _KubernetesModel(name="owner")
    plan = operator.sandbox_storage_plan(
        "unibot-workspace",
        {
            "workspaceId": "workspace_123",
            "workspaceStorageKey": "ws_123",
            "workspacePersistentVolumeClaim": "shared-workspaces",
        },
    )
    core = _PvcCore()

    await operator.ensure_pvc(
        core,
        "unibot-workspace",
        "unibot-sandboxes",
        {},
        [owner],
    )
    await operator.require_shared_workspace_pvc(core, "unibot-sandboxes", plan)

    assert [item.metadata.name for item in core.created] == ["unibot-workspace"]
    assert core.created[0].metadata.owner_references == [owner]
    assert core.read_names == ["unibot-workspace", "shared-workspaces"]
