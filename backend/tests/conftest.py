"""Pytest bootstrap: allow the known development auth secret in tests.

The module-level production app in ``tianzhou_agent_platform.main`` is created
with ``enforce_auth=True`` and refuses the known default secret. Tests import
that module, so they opt into the explicit development-secret exemption here.
Production deployments must set a unique ``UNIBOT_AUTH_SECRET`` instead.
"""

from __future__ import annotations

import os

os.environ.setdefault("UNIBOT_AUTH_ALLOW_DEV_SECRET", "true")
