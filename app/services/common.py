import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import AuditLog


def get_db():
    with SessionLocal() as db:
        yield db


def replay(db: Session, op_id: str):
    """Idempotency: if this client operation was already applied, return the stored result."""
    row = db.scalar(select(AuditLog).where(AuditLog.client_op_id == op_id))
    return json.loads(row.detail) if row and row.detail else None


def audit(db, actor, action, entity, entity_id, op_id=None, result=None):
    db.add(AuditLog(client_op_id=op_id, actor=actor[:60], action=action, entity=entity, entity_id=entity_id,
                    detail=json.dumps(result) if result is not None else None, at=datetime.now()))
