import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.forecast import demand_series, forecast
from app.api.transfers import router as transfers_router
from app.db import Base, engine
from app.models import (AuditLog, BloodBank, BloodUnit, Donor, HospitalRequest, ScreeningResult, User)
from app.schemas import CollectIn, DonorIn, RequestIn, ScreenIn
from app.security import authenticate, create_token, current_user, enforce_bank, require
from app.services.common import audit, get_db, replay

REQUIRED_TESTS = {"HIV", "HBV", "HCV", "Syphilis"}
GROUPS = {"O+", "A+", "B+", "AB+", "O-", "A-", "B-", "AB-"}
SHELF_LIFE_DAYS = 35            # illustrative; make configurable per component/country
MIN_DONOR_INTERVAL_DAYS = 56    # illustrative; align with national guidelines
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="ABBIS-Lite", version="0.2.0", lifespan=lifespan)
app.include_router(transfers_router)


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith("/app"):   # the dashboard: same-origin resources only, no inline scripts
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; object-src 'none'; "
                                                   "base-uri 'none'; frame-ancestors 'none'")
    return resp


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/app/")


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- authentication ----------
@app.post("/auth/login", tags=["auth"])
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    since = datetime.now() - timedelta(minutes=LOCKOUT_MINUTES)
    fails = db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == "login_failed", AuditLog.actor == form.username[:60], AuditLog.at >= since)) or 0
    if fails >= MAX_FAILED_LOGINS:
        raise HTTPException(429, f"Too many failed attempts. Try again in {LOCKOUT_MINUTES} minutes.")
    user = authenticate(db, form.username, form.password)
    if not user:
        audit(db, form.username, "login_failed", "user", "-")
        db.commit()
        raise HTTPException(401, "Incorrect username or password", headers={"WWW-Authenticate": "Bearer"})
    audit(db, user.username, "login", "user", user.id)
    db.commit()
    return {"access_token": create_token(user), "token_type": "bearer", "role": user.role, "bank_id": user.bank_id}


@app.get("/auth/me", tags=["auth"])
def me(user: User = Depends(current_user)):
    return {"username": user.username, "role": user.role, "bank_id": user.bank_id}


# ---------- reference data ----------
@app.get("/banks")
def banks(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [{"id": b.id, "name": b.name, "region": b.region} for b in db.scalars(select(BloodBank)).all()]


# ---------- traceability workflow ----------
@app.post("/donors")
def register_donor(body: DonorIn, db: Session = Depends(get_db), user: User = Depends(require("clinic", "manager"))):
    enforce_bank(user, body.bank_id)
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
    audit(db, user.username, "register", "donor", donor.id, body.client_op_id, result)
    db.commit()
    return result


@app.post("/units/collect")
def collect(body: CollectIn, db: Session = Depends(get_db), user: User = Depends(require("clinic", "manager"))):
    enforce_bank(user, body.bank_id)
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
    unit = BloodUnit(unit_code=unit_code, donor_id=donor.id, bank_id=body.bank_id, blood_group=donor.blood_group,
                     component=body.component, collected_on=today,
                     expires_on=today + timedelta(days=SHELF_LIFE_DAYS), status="collected")
    db.add(unit)
    db.flush()
    donor.last_donation = today
    result = {"unit_code": unit.unit_code, "status": unit.status, "expires_on": unit.expires_on.isoformat()}
    audit(db, user.username, "collect", "blood_unit", unit.id, body.client_op_id, result)
    db.commit()
    return result


@app.post("/units/{unit_code}/screen")
def screen(unit_code: str, body: ScreenIn, db: Session = Depends(get_db),
           user: User = Depends(require("clinic", "manager"))):
    unit = db.scalar(select(BloodUnit).where(BloodUnit.unit_code == unit_code))
    if not unit:
        raise HTTPException(404, "Unknown unit")
    enforce_bank(user, unit.bank_id)
    if (r := replay(db, body.client_op_id)) is not None:
        return r
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
    audit(db, user.username, "screen", "blood_unit", unit.id, body.client_op_id, result)
    db.commit()
    return result


@app.get("/inventory")
def inventory(bank_id: str, db: Session = Depends(get_db),
              user: User = Depends(require("clinic", "manager", "hospital", "coordinator"))):
    enforce_bank(user, bank_id, also=("hospital",))
    rows = db.execute(
        select(BloodUnit.blood_group, func.count())
        .where(BloodUnit.bank_id == bank_id, BloodUnit.status == "available", BloodUnit.expires_on >= date.today())
        .group_by(BloodUnit.blood_group)
    ).all()
    return {g: n for g, n in rows}


@app.post("/requests")
def create_request(body: RequestIn, db: Session = Depends(get_db),
                   user: User = Depends(require("hospital", "manager"))):
    enforce_bank(user, body.bank_id, also=("hospital",))
    if (r := replay(db, body.client_op_id)) is not None:
        return r
    if body.blood_group not in GROUPS:
        raise HTTPException(422, "Invalid blood group")
    if not db.get(BloodBank, body.bank_id):
        raise HTTPException(404, "Unknown blood bank")
    units = db.scalars(  # FEFO: first-expiry-first-out reduces wastage
        select(BloodUnit)
        .where(BloodUnit.bank_id == body.bank_id, BloodUnit.blood_group == body.blood_group,
               BloodUnit.status == "available", BloodUnit.expires_on >= date.today())
        .order_by(BloodUnit.expires_on).limit(body.units)
    ).all()
    status = "rejected" if not units else ("fulfilled" if len(units) == body.units else "partial")
    req = HospitalRequest(hospital=body.hospital, bank_id=body.bank_id, blood_group=body.blood_group,
                          units_requested=body.units, requested_at=datetime.now(), status=status)
    db.add(req)
    db.flush()
    for u in units:
        u.status = "issued"
        audit(db, user.username, "issue", "blood_unit", u.id, result={"request_id": req.id, "hospital": body.hospital})
    result = {"request_id": req.id, "status": status, "requested": body.units,
              "issued_units": [u.unit_code for u in units]}
    audit(db, user.username, "request", "hospital_request", req.id, body.client_op_id, result)
    db.commit()
    return result


@app.get("/units/{unit_code}/trace")
def trace(unit_code: str, db: Session = Depends(get_db),
          user: User = Depends(require("clinic", "manager", "coordinator"))):
    unit = db.scalar(select(BloodUnit).where(BloodUnit.unit_code == unit_code))
    if not unit:
        raise HTTPException(404, "Unknown unit")
    enforce_bank(user, unit.bank_id)
    tests = db.scalars(select(ScreeningResult).where(ScreeningResult.unit_id == unit.id)).all()
    events = db.scalars(select(AuditLog).where(AuditLog.entity_id == unit.id).order_by(AuditLog.at)).all()
    return {
        "unit": {"unit_code": unit.unit_code, "blood_group": unit.blood_group, "status": unit.status,
                 "collected_on": unit.collected_on.isoformat(), "expires_on": unit.expires_on.isoformat(),
                 "bank_id": unit.bank_id},
        "screening": [{"test": t.test, "result": t.result} for t in tests],
        "events": [{"action": e.action, "actor": e.actor, "at": e.at.isoformat()} for e in events],
    }


@app.get("/forecast")
def get_forecast(bank_id: str, blood_group: str, horizon: int = Query(7, ge=1, le=28),
                 db: Session = Depends(get_db), user: User = Depends(require("manager", "coordinator"))):
    enforce_bank(user, bank_id)
    if blood_group not in GROUPS:
        raise HTTPException(422, "Invalid blood group")
    series = demand_series(db, bank_id, blood_group)
    if len(series) < 90:
        raise HTTPException(422, "Not enough history (need at least 90 days)")
    result = forecast(series, horizon)
    stock = db.scalar(
        select(func.count()).select_from(BloodUnit)
        .where(BloodUnit.bank_id == bank_id, BloodUnit.blood_group == blood_group,
               BloodUnit.status == "available", BloodUnit.expires_on >= date.today())
    ) or 0
    demand = round(sum(d["units"] for d in result["forecast"]), 1)
    shortfall = max(0, round(demand - stock))
    result.update({
        "stock_on_hand": stock, "projected_demand": demand, "projected_shortfall": shortfall,
        "suggestion": (f"Review a transfer of about {shortfall} {blood_group} units to this bank"
                       if shortfall else "No action needed"),
        "requires_human_approval": True,
    })
    return result


# The dashboard is mounted last so it never shadows an API route.
app.mount("/app", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="dashboard")
