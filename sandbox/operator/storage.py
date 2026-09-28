from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

_STORAGE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_PVC_NAME_PATTERN = re.compile(
    r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*$"
)


@dataclass(frozen=True)
class SandboxStoragePlan:
    runtime_claim_name: str
    workspace_claim_name: str
    workspace_storage_key: str | None
    workspace_sub_path: str | None
    home_path: str
    init_directory: str | None
    storage_signature: str | None

    @property
    def is_workspace(self) -> bool:
        return self.workspace_sub_path is not None


def sandbox_storage_plan(name: str, spec: dict[str, Any]) -> SandboxStoragePlan:
    if spec.get("workspaceId") is None:
        return SandboxStoragePlan(
            runtime_claim_name=name,
            workspace_claim_name=name,
            workspace_storage_key=None,
            workspace_sub_path=None,
            home_path="/workspace",
            init_directory=None,
            storage_signature=None,
        )

    storage_key = spec.get("workspaceStorageKey")
    if not isinstance(storage_key, str) or not _STORAGE_KEY_PATTERN.fullmatch(storage_key):
        raise ValueError("workspaceStorageKey must match ^[A-Za-z0-9_-]+$")
    if len(storage_key) > 160:
        raise ValueError("workspaceStorageKey cannot exceed 160 characters")

    claim_name = spec.get("workspacePersistentVolumeClaim")
    if (
        not isinstance(claim_name, str)
        or len(claim_name) > 253
        or not _PVC_NAME_PATTERN.fullmatch(claim_name)
    ):
        raise ValueError("workspacePersistentVolumeClaim must be a valid Kubernetes PVC name")

    sub_path = f"workspaces/{storage_key}/files"
    signature = hashlib.sha256(f"{claim_name}:{sub_path}".encode()).hexdigest()[:16]
    return SandboxStoragePlan(
        runtime_claim_name=name,
        workspace_claim_name=claim_name,
        workspace_storage_key=storage_key,
        workspace_sub_path=sub_path,
        home_path="/runtime",
        init_directory=f"/shared-workspaces/{sub_path}",
        storage_signature=signature,
    )
