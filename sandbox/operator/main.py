from __future__ import annotations

import os
from typing import Any

import kopf
from kubernetes_asyncio import client, config
from kubernetes_asyncio.client import ApiException

from storage import SandboxStoragePlan, sandbox_storage_plan

GROUP = "sandbox.unibot.ai"
VERSION = "v1alpha1"
PLURAL = "usersandboxes"
SANDBOXD_PORT = 8080
DEFAULT_STORAGE_CLASS = os.getenv("SANDBOX_STORAGE_CLASS", "longhorn")


@kopf.on.startup()
async def startup(**_: Any) -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        await config.load_kube_config()


@kopf.on.create(GROUP, VERSION, PLURAL)
@kopf.on.update(GROUP, VERSION, PLURAL)
async def reconcile(
    spec: dict[str, Any],
    name: str,
    namespace: str,
    body: dict[str, Any],
    patch: kopf.Patch,
    **_: Any,
) -> None:
    desired_state = spec.get("desiredState", "Running")
    core = client.CoreV1Api()
    if desired_state == "Stopped":
        await delete_if_present(core.delete_namespaced_pod, name, namespace)
        await delete_if_present(core.delete_namespaced_service, name, namespace)
        patch.status.update({"phase": "Stopped", "endpoint": None, "message": None})
        return

    try:
        storage_plan = sandbox_storage_plan(name, spec)
    except ValueError as exc:
        raise kopf.PermanentError(str(exc)) from exc

    owner_references = [
        client.V1OwnerReference(
            api_version=f"{GROUP}/{VERSION}",
            kind="UserSandbox",
            name=name,
            uid=body["metadata"]["uid"],
            controller=True,
            block_owner_deletion=True,
        )
    ]
    await ensure_pvc(core, name, namespace, spec, owner_references)
    await require_shared_workspace_pvc(core, namespace, storage_plan)
    await ensure_service(core, name, namespace, owner_references)
    await ensure_pod(core, name, namespace, spec, owner_references, storage_plan)
    status = {
        "phase": "Provisioning",
        "endpoint": None if storage_plan.is_workspace else service_endpoint(name, namespace),
        "message": None,
    }
    status.update(workspace_mount_status(storage_plan, ready=False))
    patch.status.update(status)


@kopf.timer(GROUP, VERSION, PLURAL, interval=5.0, sharp=True)
async def refresh_status(
    spec: dict[str, Any],
    name: str,
    namespace: str,
    patch: kopf.Patch,
    **_: Any,
) -> None:
    if spec.get("desiredState", "Running") == "Stopped":
        return
    try:
        storage_plan = sandbox_storage_plan(name, spec)
    except ValueError as exc:
        patch.status.update(
            {
                "phase": "Error",
                "endpoint": None,
                "message": str(exc),
                "workspaceMountReady": False,
            }
        )
        return
    core = client.CoreV1Api()
    try:
        pod = await core.read_namespaced_pod(name, namespace)
    except ApiException as exc:
        if exc.status == 404:
            status = {"phase": "Provisioning", "endpoint": None}
            status.update(workspace_mount_status(storage_plan, ready=False))
            patch.status.update(status)
            return
        raise
    if storage_plan.is_workspace:
        annotations = pod.metadata.annotations or {}
        if annotations.get("sandbox.unibot.ai/storage-signature") != storage_plan.storage_signature:
            status = {"phase": "Provisioning", "endpoint": None, "message": None}
            status.update(workspace_mount_status(storage_plan, ready=False))
            patch.status.update(status)
            return
    ready = any(condition.type == "Ready" and condition.status == "True" for condition in pod.status.conditions or [])
    if ready:
        status = {
            "phase": "Ready",
            "endpoint": service_endpoint(name, namespace),
            "message": None,
        }
        status.update(workspace_mount_status(storage_plan, ready=True))
        patch.status.update(status)
    elif pod.status.phase == "Failed":
        status = {
            "phase": "Error",
            "endpoint": None,
            "message": pod.status.message or "Sandbox pod failed",
        }
        status.update(workspace_mount_status(storage_plan, ready=False))
        patch.status.update(status)
    else:
        status = {
            "phase": "Provisioning",
            "endpoint": None if storage_plan.is_workspace else service_endpoint(name, namespace),
        }
        status.update(workspace_mount_status(storage_plan, ready=False))
        patch.status.update(status)


async def ensure_pvc(
    core: client.CoreV1Api,
    name: str,
    namespace: str,
    spec: dict[str, Any],
    owner_references: list[client.V1OwnerReference],
) -> None:
    body = client.V1PersistentVolumeClaim(
        metadata=client.V1ObjectMeta(name=name, namespace=namespace, owner_references=owner_references),
        spec=client.V1PersistentVolumeClaimSpec(
            access_modes=["ReadWriteOncePod"],
            storage_class_name=spec.get("storageClassName") or DEFAULT_STORAGE_CLASS,
            resources=client.V1VolumeResourceRequirements(
                requests={"storage": spec.get("workspaceSize", "20Gi")}
            ),
        ),
    )
    await create_if_missing(core.read_namespaced_persistent_volume_claim, core.create_namespaced_persistent_volume_claim, name, namespace, body)


async def ensure_service(
    core: client.CoreV1Api,
    name: str,
    namespace: str,
    owner_references: list[client.V1OwnerReference],
) -> None:
    labels = sandbox_labels(name)
    body = client.V1Service(
        metadata=client.V1ObjectMeta(name=name, namespace=namespace, owner_references=owner_references),
        spec=client.V1ServiceSpec(
            selector=labels,
            ports=[client.V1ServicePort(name="http", port=SANDBOXD_PORT, target_port=SANDBOXD_PORT)],
        ),
    )
    await create_if_missing(core.read_namespaced_service, core.create_namespaced_service, name, namespace, body)


async def require_shared_workspace_pvc(
    core: client.CoreV1Api,
    namespace: str,
    storage_plan: SandboxStoragePlan,
) -> None:
    if not storage_plan.is_workspace:
        return
    try:
        await core.read_namespaced_persistent_volume_claim(
            storage_plan.workspace_claim_name,
            namespace,
        )
    except ApiException as exc:
        if exc.status != 404:
            raise
        raise kopf.TemporaryError(
            f"Shared workspace PVC {storage_plan.workspace_claim_name!r} does not exist",
            delay=10,
        ) from exc


async def ensure_pod(
    core: client.CoreV1Api,
    name: str,
    namespace: str,
    spec: dict[str, Any],
    owner_references: list[client.V1OwnerReference],
    storage_plan: SandboxStoragePlan,
) -> None:
    resources = spec.get("resources") or {}
    limits = {
        "cpu": resources.get("cpu", "1"),
        "memory": resources.get("memory", "2Gi"),
        "ephemeral-storage": resources.get("ephemeralStorage", "2Gi"),
    }
    labels = sandbox_labels(name)
    annotations = (
        {"sandbox.unibot.ai/storage-signature": storage_plan.storage_signature}
        if storage_plan.storage_signature is not None
        else None
    )
    environment = [
        client.V1EnvVar(name="HOME", value=storage_plan.home_path),
        client.V1EnvVar(name="SANDBOX_WORKSPACE", value="/workspace"),
        client.V1EnvVar(name="SANDBOX_RUNTIME", value=storage_plan.home_path),
    ]
    volume_mounts = [
        client.V1VolumeMount(
            name="workspace",
            mount_path="/workspace",
            sub_path=storage_plan.workspace_sub_path,
        ),
        client.V1VolumeMount(name="tmp", mount_path="/tmp"),
    ]
    volumes = [
        client.V1Volume(
            name="workspace",
            persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                claim_name=storage_plan.workspace_claim_name
            ),
        ),
        client.V1Volume(name="tmp", empty_dir=client.V1EmptyDirVolumeSource(size_limit="512Mi")),
    ]
    init_containers = None
    if storage_plan.is_workspace:
        assert storage_plan.init_directory is not None
        volume_mounts.insert(0, client.V1VolumeMount(name="runtime", mount_path="/runtime"))
        volumes.insert(
            0,
            client.V1Volume(
                name="runtime",
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                    claim_name=storage_plan.runtime_claim_name
                ),
            ),
        )
        init_containers = [
            client.V1Container(
                name="workspace-init",
                image=spec["image"],
                image_pull_policy=spec.get("imagePullPolicy", "IfNotPresent"),
                command=["mkdir", "-p", storage_plan.init_directory],
                resources=client.V1ResourceRequirements(
                    requests={"cpu": "10m", "memory": "16Mi"},
                    limits={"cpu": "100m", "memory": "64Mi"},
                ),
                security_context=client.V1SecurityContext(
                    allow_privilege_escalation=False,
                    privileged=False,
                    read_only_root_filesystem=True,
                    capabilities=client.V1Capabilities(drop=["ALL"]),
                ),
                volume_mounts=[
                    client.V1VolumeMount(name="workspace", mount_path="/shared-workspaces")
                ],
            )
        ]
    body = client.V1Pod(
        metadata=client.V1ObjectMeta(
            name=name,
            namespace=namespace,
            labels=labels,
            annotations=annotations,
            owner_references=owner_references,
        ),
        spec=client.V1PodSpec(
            runtime_class_name=spec.get("runtimeClassName", "gvisor"),
            automount_service_account_token=False,
            restart_policy="Always",
            termination_grace_period_seconds=5,
            init_containers=init_containers,
            security_context=client.V1PodSecurityContext(
                run_as_non_root=True,
                run_as_user=1000,
                run_as_group=1000,
                fs_group=1000,
                seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
            ),
            containers=[
                client.V1Container(
                    name="sandboxd",
                    image=spec["image"],
                    image_pull_policy=spec.get("imagePullPolicy", "IfNotPresent"),
                    ports=[client.V1ContainerPort(name="http", container_port=SANDBOXD_PORT)],
                    env=environment,
                    resources=client.V1ResourceRequirements(
                        requests={"cpu": "100m", "memory": "128Mi"},
                        limits=limits,
                    ),
                    security_context=client.V1SecurityContext(
                        allow_privilege_escalation=False,
                        privileged=False,
                        read_only_root_filesystem=True,
                        capabilities=client.V1Capabilities(drop=["ALL"]),
                    ),
                    readiness_probe=client.V1Probe(
                        http_get=client.V1HTTPGetAction(path="/health", port="http"),
                        initial_delay_seconds=2,
                        period_seconds=3,
                    ),
                    volume_mounts=volume_mounts,
                )
            ],
            volumes=volumes,
        ),
    )
    await create_pod_if_missing_or_stale(
        core,
        name,
        namespace,
        body,
        storage_signature=storage_plan.storage_signature,
    )


async def create_pod_if_missing_or_stale(
    core: client.CoreV1Api,
    name: str,
    namespace: str,
    body: client.V1Pod,
    *,
    storage_signature: str | None,
) -> None:
    try:
        existing = await core.read_namespaced_pod(name, namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise
        await core.create_namespaced_pod(namespace, body)
        return
    if storage_signature is None:
        return
    annotations = existing.metadata.annotations or {}
    if annotations.get("sandbox.unibot.ai/storage-signature") == storage_signature:
        return
    await core.delete_namespaced_pod(name, namespace, grace_period_seconds=0)
    raise kopf.TemporaryError("Replacing sandbox pod with workspace-safe mounts", delay=1)


async def create_if_missing(read: Any, create: Any, name: str, namespace: str, body: Any) -> None:
    try:
        await read(name, namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise
        await create(namespace, body)


async def delete_if_present(delete: Any, name: str, namespace: str) -> None:
    try:
        await delete(name, namespace, grace_period_seconds=0)
    except ApiException as exc:
        if exc.status != 404:
            raise


def sandbox_labels(name: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/name": "unibot-sandbox",
        "sandbox.unibot.ai/name": name,
        "sandbox.unibot.ai/workload": "true",
    }


def service_endpoint(name: str, namespace: str) -> str:
    return f"{name}.{namespace}.svc.cluster.local"


def workspace_mount_status(
    storage_plan: SandboxStoragePlan,
    *,
    ready: bool,
) -> dict[str, Any]:
    if not storage_plan.is_workspace:
        return {}
    assert storage_plan.workspace_storage_key is not None
    return {
        "workspaceMountReady": ready,
        "workspaceStorageKey": storage_plan.workspace_storage_key,
        "workspacePersistentVolumeClaim": storage_plan.workspace_claim_name,
    }
