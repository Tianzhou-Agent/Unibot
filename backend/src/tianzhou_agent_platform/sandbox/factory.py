from __future__ import annotations

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.sandbox.drivers import (
    KubernetesSandboxDriver,
    LocalProcessSandboxDriver,
)
from tianzhou_agent_platform.sandbox.service import SandboxService


def create_sandbox_service(
    settings: AgentSettings,
    repository: InMemoryRepository,
) -> SandboxService:
    if settings.sandbox_driver == "local":
        driver = LocalProcessSandboxDriver(
            settings.sandbox_workspace_root,
            output_limit_bytes=settings.sandbox_output_limit_bytes,
        )
    else:
        token_file = settings.sandbox_kubernetes_token_file
        if not token_file.exists():
            raise PlatformError(
                "DEPENDENCY_FAILED",
                f"Kubernetes service-account token was not found at {token_file}",
                status_code=503,
                source="sandbox",
            )
        driver = KubernetesSandboxDriver(
            api_url=settings.sandbox_kubernetes_api_url,
            namespace=settings.sandbox_kubernetes_namespace,
            token=token_file.read_text(encoding="utf-8").strip(),
            ca_file=(
                str(settings.sandbox_kubernetes_ca_file)
                if settings.sandbox_kubernetes_ca_file.exists()
                else None
            ),
            runtime_class=settings.sandbox_runtime_class,
        )
    return SandboxService(repository, driver, default_image=settings.sandbox_default_image)
