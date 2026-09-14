from tianzhou_agent_platform.aina.skill.models import SkillRecord
from tianzhou_agent_platform.aina.tool.models import ToolRecord


def capability_visible(
    record: ToolRecord | SkillRecord,
    *,
    user_id: str,
    tenant_id: str,
    auth_enforced: bool = True,
    is_admin: bool = False,
) -> bool:
    """Apply the same registry boundary to discovery and runtime execution."""
    if not auth_enforced or is_admin or record.visibility == "public":
        return True
    # Legacy non-public records have no trusted ownership and fail closed.
    if not record.owner_user_id or not record.tenant_id or record.tenant_id != tenant_id:
        return False
    return record.visibility == "tenant" or record.owner_user_id == user_id
