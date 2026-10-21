import json
import math
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.forecast import demand_series, forecast
from app.models import BloodBank, BloodUnit, TransferSuggestion
from app.services.common import audit, get_db, replay

router = APIRouter(prefix="/transfers", tags=["transfers"])
GROUPS = {"O+", "A+", "B+", "AB+", "O-", "A-", "B-", "AB-"}
SAFETY_MARGIN = 1.2        # a donor bank keeps 20% above its own forecast demand
MIN_SHELF_LIFE_DAYS = 7    # never ship units about to expire


class DecisionIn(BaseModel):
    client_op_id: str
    decision: Literal["approve", "reject"]
    reason: str | None = None


def stock_of(db, bank_id, group):
    return db.scalar(select(func.count()).select_from(BloodUnit).where(
        BloodUnit.bank_id == bank_id, BloodUnit.blood_group == group,
        BloodUnit.status == "available", BloodUnit.expires_on >= date.today())) or 0


def outlook(db, bank_id, group, horizon):
    series = demand_series(db, bank_id, group)
    if len(series) < 90:
        return None
    f = forecast(series, horizon)
    bt = f["backtest"].get(f["model"]) or f["backtest"]["moving_avg_14d"]
    return {"stock": stock_of(db, bank_id, group), "demand": round(sum(d["units"] for d in f["forecast"]), 1),
            "model": f["model"], "low_confidence": f["low_confidence"], "wape": bt["wape"]}


def serialize(s: TransferSuggestion):
    return {"id": s.id, "from_bank_id": s.from_bank_id, "to_bank_id": s.to_bank_id, "blood_group": s.blood_group,
            "units": s.units, "status": s.status, "model": s.model, "rationale": json.loads(s.rationale or "{}"),
            "decided_by": s.decided_by, "decision_reason": s.decision_reason,
            "requires_human_approval": s.status == "proposed"}


@router.post("/suggest")
def suggest(to_bank_id: str, blood_group: str, horizon: int = Query(7, ge=1, le=28), db: Session = Depends(get_db)):
    if blood_group not in GROUPS:
        raise HTTPException(422, "Invalid blood group")
    if not db.get(BloodBank, to_bank_id):
        raise HTTPException(404, "Unknown blood bank")
    existing = db.scalar(select(TransferSuggestion).where(
        TransferSuggestion.to_bank_id == to_bank_id, TransferSuggestion.blood_group == blood_group,
        TransferSuggestion.status == "proposed"))
    if existing:
        return serialize(existing)
    target = outlook(db, to_bank_id, blood_group, horizon)
    if not target:
        raise HTTPException(422, "Not enough demand history (need at least 90 days)")
    shortfall = max(0, round(target["demand"] - target["stock"]))
    if shortfall == 0:
        return {"suggestion": None, "message": "No shortfall projected"}
    best = None
    for bank in db.scalars(select(BloodBank).where(BloodBank.id != to_bank_id)).all():
        o = outlook(db, bank.id, blood_group, horizon)
        if not o:
            continue
        surplus = math.floor(o["stock"] - SAFETY_MARGIN * o["demand"])
        if surplus > 0 and (best is None or surplus > best[1]):
            best = (bank, surplus, o)
    if not best:
        return {"suggestion": None, "shortfall": shortfall,
                "message": "Shortfall projected but no bank has a safe surplus. Escalate to the national coordinator."}
    bank, surplus, source = best
    qty = min(shortfall, surplus)
    rationale = {"horizon_days": horizon, "destination": target, "source": source, "shortfall": shortfall,
                 "source_surplus_after_safety_margin": surplus,
                 "explanation": (f"Destination stock {target['stock']} vs forecast demand {target['demand']} "
                                 f"over {horizon} days. Source holds {source['stock']} against forecast demand "
                                 f"{source['demand']} and keeps a {int((SAFETY_MARGIN - 1) * 100)}% margin.")}
    s = TransferSuggestion(from_bank_id=bank.id, to_bank_id=to_bank_id, blood_group=blood_group, units=qty,
                           rationale=json.dumps(rationale), model=target["model"])
    db.add(s)
    db.commit()
    return serialize(s)


@router.get("")
def list_transfers(status: str | None = None, db: Session = Depends(get_db)):
    q = select(TransferSuggestion).order_by(TransferSuggestion.created_at.desc())
    if status:
        q = q.where(TransferSuggestion.status == status)
    return [serialize(s) for s in db.scalars(q).all()]


@router.post("/{suggestion_id}/decide")
def decide(suggestion_id: str, body: DecisionIn, db: Session = Depends(get_db),
           x_actor: str = Header(default="demo")):
    if (r := replay(db, body.client_op_id)) is not None:
        return r
    s = db.get(TransferSuggestion, suggestion_id)
    if not s:
        raise HTTPException(404, "Unknown suggestion")
    if s.status != "proposed":
        raise HTTPException(409, f"Suggestion already {s.status}")
    if body.decision == "reject" and not (body.reason and body.reason.strip()):
        raise HTTPException(422, "A reason is required when rejecting")
    moved = []
    if body.decision == "approve":
        # Ship the freshest units: the source keeps its older stock for local FEFO use.
        units = db.scalars(select(BloodUnit).where(
            BloodUnit.bank_id == s.from_bank_id, BloodUnit.blood_group == s.blood_group,
            BloodUnit.status == "available",
            BloodUnit.expires_on >= date.today() + timedelta(days=MIN_SHELF_LIFE_DAYS))
            .order_by(BloodUnit.expires_on.desc()).limit(s.units)).all()
        if len(units) < s.units:
            raise HTTPException(409, f"Source stock changed: only {len(units)} eligible units left. Re-run suggest.")
        for u in units:
            u.bank_id = s.to_bank_id
            moved.append(u.unit_code)
            audit(db, x_actor, "transfer", "blood_unit", u.id,
                  result={"suggestion_id": s.id, "from": s.from_bank_id, "to": s.to_bank_id})
    s.status = "approved" if body.decision == "approve" else "rejected"
    s.decided_by, s.decided_at, s.decision_reason = x_actor, datetime.now(), body.reason
    result = {"suggestion_id": s.id, "status": s.status, "moved_units": moved}
    audit(db, x_actor, "decide_transfer", "transfer_suggestion", s.id, body.client_op_id, result)
    db.commit()
    return result
