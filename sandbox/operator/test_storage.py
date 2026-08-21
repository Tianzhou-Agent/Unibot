import pytest

from storage import sandbox_storage_plan


def test_standalone_uses_owned_runtime_claim_as_workspace() -> None:
    plan = sandbox_storage_plan("unibot-standalone", {})

    assert plan.is_workspace is False
    assert plan.runtime_claim_name == "unibot-standalone"
    assert plan.workspace_claim_name == "unibot-standalone"
    assert plan.workspace_sub_path is None
    assert plan.home_path == "/workspace"
    assert plan.init_directory is None


def test_workspace_uses_shared_claim_subpath_and_separate_runtime() -> None:
    plan = sandbox_storage_plan(
        "unibot-workspace",
        {
            "workspaceId": "workspace_123",
            "workspaceStorageKey": "ws_ABC-123",
            "workspacePersistentVolumeClaim": "shared-workspaces",
        },
    )

    assert plan.is_workspace is True
    assert plan.runtime_claim_name == "unibot-workspace"
    assert plan.workspace_claim_name == "shared-workspaces"
    assert plan.workspace_sub_path == "workspaces/ws_ABC-123/files"
    assert plan.home_path == "/runtime"
    assert plan.init_directory == "/shared-workspaces/workspaces/ws_ABC-123/files"
    assert plan.storage_signature is not None


@pytest.mark.parametrize(
    "storage_key",
    ["../other", "nested/key", "key with spaces", ""],
)
def test_workspace_rejects_unsafe_server_storage_key(storage_key: str) -> None:
    with pytest.raises(ValueError, match="workspaceStorageKey"):
        sandbox_storage_plan(
            "unibot-workspace",
            {
                "workspaceId": "workspace_123",
                "workspaceStorageKey": storage_key,
                "workspacePersistentVolumeClaim": "shared-workspaces",
            },
        )


@pytest.mark.parametrize("claim_name", ["../claim", "UPPERCASE", "bad..claim", "-bad"])
def test_workspace_rejects_invalid_shared_claim_name(claim_name: str) -> None:
    with pytest.raises(ValueError, match="workspacePersistentVolumeClaim"):
        sandbox_storage_plan(
            "unibot-workspace",
            {
                "workspaceId": "workspace_123",
                "workspaceStorageKey": "ws_123",
                "workspacePersistentVolumeClaim": claim_name,
            },
        )
