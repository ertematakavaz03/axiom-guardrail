from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.db.models import AuditLog
from apps.api.app.security.redaction import redact_secrets


def add_audit(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None,
    project_id: uuid.UUID | None,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    metadata: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            organization_id=organization_id,
            user_id=user_id,
            project_id=project_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            audit_metadata=redact_secrets(metadata or {}),
        )
    )
