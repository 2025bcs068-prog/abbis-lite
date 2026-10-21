import json
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, SessionLocal, engine
from app.models import (AuditLog, BloodBank, BloodUnit, Donor,
                        HospitalRequest, ScreeningResult)
from app.schemas import CollectIn, DonorIn, RequestIn, ScreenIn

REQUIRED_TESTS = {"HIV", "HBV", "HCV", "Syphilis"}
GROUPS = {"O+", "A+", "B+", "AB+", "O-", "A-", "B-", "AB-"}
SHELF_LIFE_DAYS = 35            # illustrative; make configurable per component/country
MIN_DONOR_INTERVAL_DAYS = 56    # illustrative; align with national guidelines

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield

app = FastAPI(title="ABBIS-Lite", version="0.1.0", lifespan=lifespan)

def get_db():
    with SessionLocal() as db:
        yield db

def replay(db: Session, op_id: str):
    """Idempotency: if this client operation was already applied, return the stored result."""
    row = db.scalar(select(AuditLog).where(AuditLog.client_op_id == op_id))
    return json.loads(row.detail) if row and row.detail else None

def audit(db, actor, action, entity, entity_id, op_id=None, result=None):
    db.add(AuditLog(client_op_id=op_id, actor=actor, action=action, entity=entity,
                    entity_id=entity_id, detail=json.dumps(result) if result is not None else None))

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/units/collect")
def collect(body: CollectIn, db: Session = Depends(get_db), x_actor: str = Header(default="demo")):
    if (r := replay(db, body.client_op_id)) is not None:
        return r
    donor = db.scalar(select(Donor).where(Donor.code == body.donor_code))
    if not donor:
        raise HTTPException(404, "Unknown donor")
    if not db.get(BloodBank, body.bank_id):
        raise HTTPException(404, "Unknown blood bank")
    unit_code = body.unit_code or "U" + uuid.uuid4().hex[:10].upper()
    if db.scalar(select(BloodUnit).where(BloodUnit.unit_code == unit_code)):
        raise HTTPException(409, "Unit code already exists")
    today = date.today()
    if donor.last_donation and (today - donor.last_donation).days < MIN_DONOR_INTERVAL_DAYS:
        raise HTTPException(409, f"Donor must wait {MIN_DONOR_INTERVAL_DAYS} days between donations")
    unit = BloodUnit(unit_code=unit_code, donor_id=donor.id,
                     bank_id=body.bank_id, blood_group=donor.blood_group, component=body.component,
                     collected_on=today, expires_on=today + timedelta(days=SHELF_LIFE_DAYS),
                     status="collected")
    db.add(unit)
    db.flush()
    donor.last_donation = today
    result = {"unit_code": unit.unit_code, "status": unit.status, "expires_on": unit.expires_on.isoformat()}
    audit(db, x_actor, "collect", "blood_unit", unit.id, body.client_op_id, result)
    db.commit()
    return result

@app.post("/units/{unit_code}/screen")
def screen(unit_code: str, body: ScreenIn, db: Session = Depends(get_db), x_actor: str = Header(default="demo")):
    if (r := replay(db, body.client_op_id)) is not None:
        return r
    unit = db.scalar(select(BloodUnit).where(BloodUnit.unit_code == unit_code))
    if not unit:
        raise HTTPException(404, "Unknown unit")
    if unit.status != "collected":
        raise HTTPException(409, f"Unit is '{unit.status}', expected 'collected'")
    missing = REQUIRED_TESTS - set(body.results)
    if missing:
        raise HTTPException(422, f"Missing required tests: {sorted(missing)}")
    if any(v not in ("negative", "positive") for v in body.results.values()):
        raise HTTPException(422, "Results must be 'negative' or 'positive'")
    now = datetime.now()
    for test, res in body.results.items():
        db.add(ScreeningResult(unit_id=unit.id, test=test, result=res, tested_at=now))
    unit.status = "discarded" if "positive" in body.results.values() else "available"
    result = {"unit_code": unit.unit_code, "status": unit.status}
    audit(db, x_actor, "screen", "blood_unit", unit.id, body.client_op_id, result)
    db.commit()
    return result

@app.get("/inventory")
def inventory(bank_id: str, db: Session = Depends(get_db)):
    rows = db.execute(
        select(BloodUnit.blood_group, func.count())
        .where(BloodUnit.bank_id == bank_id, BloodUnit.status == "available",
               BloodUnit.expires_on >= date.today())
        .group_by(BloodUnit.blood_group)
    ).all()
    return {g: n for g, n in rows}

@app.post("/requests")
def create_request(body: RequestIn, db: Session = Depends(get_db), x_actor: str = Header(default="demo")):
    if (r := replay(db, body.client_op_id)) is not None:
        return r
    if body.blood_group not in GROUPS:
        raise HTTPException(422, "Invalid blood group")
    if not db.get(BloodBank, body.bank_id):
        raise HTTPException(404, "Unknown blood bank")
    # FEFO: first-expiry-first-out reduces wastage
    units = db.scalars(
        select(BloodUnit)
        .where(BloodUnit.bank_id == body.bank_id, BloodUnit.blood_group == body.blood_group,
               BloodUnit.status == "available", BloodUnit.expires_on >= date.today())
        .order_by(BloodUnit.expires_on)
        .limit(body.units)
    ).all()
    status = "rejected" if not units else ("fulfilled" if len(units) == body.units else "partial")
    req = HospitalRequest(hospital=body.hospital, bank_id=body.bank_id, blood_group=body.blood_group,
                          units_requested=body.units, requested_at=datetime.now(), status=status)
    db.add(req)
    db.flush()
    for u in units:
        u.status = "issued"
        audit(db, x_actor, "issue", "blood_unit", u.id, result={"request_id": req.id, "hospital": body.hospital})
    result = {"request_id": req.id, "status": status, "requested": body.units,
              "issued_units": [u.unit_code for u in units]}
    audit(db, x_actor, "request", "hospital_request", req.id, body.client_op_id, result)
    db.commit()
    return result

@app.get("/units/{unit_code}/trace")
def trace(unit_code: str, db: Session = Depends(get_db)):
    unit = db.scalar(select(BloodUnit).where(BloodUnit.unit_code == unit_code))
    if not unit:
        raise HTTPException(404, "Unknown unit")
    tests = db.scalars(select(ScreeningResult).where(ScreeningResult.unit_id == unit.id)).all()
    events = db.scalars(select(AuditLog).where(AuditLog.entity_id == unit.id).order_by(AuditLog.at)).all()
    return {
        "unit": {"unit_code": unit.unit_code, "blood_group": unit.blood_group, "status": unit.status,
                 "collected_on": unit.collected_on.isoformat(), "expires_on": unit.expires_on.isoformat(),
                 "bank_id": unit.bank_id},
        "screening": [{"test": t.test, "result": t.result} for t in tests],
        "events": [{"action": e.action, "actor": e.actor, "at": e.at.isoformat()} for e in events],
    }

@app.get("/banks")
def banks(db: Session = Depends(get_db)):
    return [{"id": b.id, "name": b.name, "region": b.region} for b in db.scalars(select(BloodBank)).all()]

@app.post("/donors")
def register_donor(body: DonorIn, db: Session = Depends(get_db), x_actor: str = Header(default="demo")):
    if (r := replay(db, body.client_op_id)) is not None:
        return r
    if body.blood_group not in GROUPS:
        raise HTTPException(422, "Invalid blood group")
    if not db.get(BloodBank, body.bank_id):
        raise HTTPException(404, "Unknown blood bank")
    if db.scalar(select(Donor).where(Donor.code == body.code)):
        raise HTTPException(409, "Donor code already exists")
    donor = Donor(code=body.code, blood_group=body.blood_group, birth_year=body.birth_year, bank_id=body.bank_id)
    db.add(donor)
    db.flush()
    result = {"donor_code": donor.code}
    audit(db, x_actor, "register", "donor", donor.id, body.client_op_id, result)
    db.commit()
    return result
