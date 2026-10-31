from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AuditEvent


def record(session: Session, actor: str, action: str, entity_type: str,
           entity_id: int | None = None, **detail: Any) -> None:
    """Append-only audit trail. Detail holds IDs and settings, never full source."""
    session.add(AuditEvent(actor=actor, action=action, entity_type=entity_type,
                           entity_id=entity_id, detail=detail))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_hash(obj: Any) -> str:
    return sha256_text(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str))
