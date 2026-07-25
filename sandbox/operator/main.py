from __future__ import annotations

import os
from typing import Any

import kopf
from kubernetes_asyncio import client, config
from kubernetes_asyncio.client import ApiException

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
    await ensure_service(core, name, namespace, owner_references)
    await ensure_pod(core, name, namespace, spec, owner_references)
    patch.status.update({"phase": "Provisioning", "endpoint": service_endpoint(name, namespace), "message": None})


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
    core = client.CoreV1Api()
    try:
        pod = await core.read_namespaced_pod(name, namespace)
    except ApiException as exc:
        if exc.status == 404:
            patch.status.update({"phase": "Provisioning", "endpoint": None})
            return
        raise
    ready = any(condition.type == "Ready" and condition.status == "True" for condition in pod.status.conditions or [])
    if ready:
        patch.status.update(
            {
                "phase": "Ready",
                "endpoint": service_endpoint(name, namespace),
                "message": None,
            }
        )
    elif pod.status.phase == "Failed":
        patch.status.update(
            {
                "phase": "Error",
                "endpoint": None,
                "message": pod.status.message or "Sandbox pod failed",
            }
        )
    else:
        patch.status.update({"phase": "Provisioning", "endpoint": service_endpoint(name, namespace)})


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


async def ensure_pod(
    core: client.CoreV1Api,
    name: str,
    namespace: str,
    spec: dict[str, Any],
    owner_references: list[client.V1OwnerReference],
) -> None:
    resources = spec.get("resources") or {}
    limits = {
        "cpu": resources.get("cpu", "1"),
        "memory": resources.get("memory", "2Gi"),
        "ephemeral-storage": resources.get("ephemeralStorage", "2Gi"),
    }
    labels = sandbox_labels(name)
    body = client.V1Pod(
        metadata=client.V1ObjectMeta(
            name=name,
            namespace=namespace,
            labels=labels,
            owner_references=owner_references,
        ),
        spec=client.V1PodSpec(
            runtime_class_name=spec.get("runtimeClassName", "gvisor"),
            automount_service_account_token=False,
            restart_policy="Always",
            termination_grace_period_seconds=5,
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
                    env=[
                        client.V1EnvVar(name="HOME", value="/workspace"),
                        client.V1EnvVar(name="SANDBOX_WORKSPACE", value="/workspace"),
                    ],
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
                    volume_mounts=[
                        client.V1VolumeMount(name="workspace", mount_path="/workspace"),
                        client.V1VolumeMount(name="tmp", mount_path="/tmp"),
                    ],
                )
            ],
            volumes=[
                client.V1Volume(
                    name="workspace",
                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name=name),
                ),
                client.V1Volume(name="tmp", empty_dir=client.V1EmptyDirVolumeSource(size_limit="512Mi")),
            ],
        ),
    )
    await create_if_missing(core.read_namespaced_pod, core.create_namespaced_pod, name, namespace, body)


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
