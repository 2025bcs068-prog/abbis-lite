#!/usr/bin/env python3
"""Apply JWT authentication, roles and authenticated demos/tests to ABBIS-Lite.

Run from the repo root (~/abbis-lite) after:  pip install pyjwt python-multipart
Originals of every file it replaces are saved in .apply_jwt_backup/ (gitignored).
It keeps the dashboard additions (static mount, CSP headers, redirect to /app/).
"""
import pathlib
import shutil
import sys

ROOT = pathlib.Path(".")
if not (ROOT / "app" / "main.py").exists():
    sys.exit("Run this from the repo root (the folder that contains app/ and tests/).")
if not (ROOT / "app" / "static" / "index.html").exists():
    sys.exit("app/static/index.html is missing. Create the dashboard files first.")
try:
    import jwt  # noqa: F401
    import multipart  # noqa: F401
except ImportError:
    sys.exit("Missing packages. Run:  pip install pyjwt python-multipart")

BACKUP = ROOT / ".apply_jwt_backup"

FILES = {}

FILES["app/models/__init__.py"] = r'''import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def uid() -> str:
    return str(uuid.uuid4())


class BloodBank(Base):
    __tablename__ = "blood_banks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    region: Mapped[str] = mapped_column(String(60))
    country: Mapped[str] = mapped_column(String(60))


class Donor(Base):
    __tablename__ = "donors"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    code: Mapped[str] = mapped_column(String(20), unique=True)  # pseudonymous ID
    blood_group: Mapped[str] = mapped_column(String(3))
    birth_year: Mapped[int] = mapped_column(Integer)
    bank_id: Mapped[str] = mapped_column(ForeignKey("blood_banks.id"))
    last_donation: Mapped[date | None] = mapped_column(Date, nullable=True)


class BloodUnit(Base):
    __tablename__ = "blood_units"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    unit_code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    donor_id: Mapped[str] = mapped_column(ForeignKey("donors.id"))
    bank_id: Mapped[str] = mapped_column(ForeignKey("blood_banks.id"), index=True)
    blood_group: Mapped[str] = mapped_column(String(3), index=True)
    component: Mapped[str] = mapped_column(String(20), default="whole_blood")
    collected_on: Mapped[date] = mapped_column(Date)
    expires_on: Mapped[date] = mapped_column(Date)
    # collected -> screened -> available -> reserved -> issued | discarded
    status: Mapped[str] = mapped_column(String(15), default="collected", index=True)


class ScreeningResult(Base):
    __tablename__ = "screening_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    unit_id: Mapped[str] = mapped_column(ForeignKey("blood_units.id"), index=True)
    test: Mapped[str] = mapped_column(String(20))    # HIV, HBV, HCV, Syphilis
    result: Mapped[str] = mapped_column(String(10))  # negative | positive
    tested_at: Mapped[datetime] = mapped_column(DateTime)


class HospitalRequest(Base):
    __tablename__ = "hospital_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    hospital: Mapped[str] = mapped_column(String(120))
    bank_id: Mapped[str] = mapped_column(ForeignKey("blood_banks.id"), index=True)
    blood_group: Mapped[str] = mapped_column(String(3), index=True)
    units_requested: Mapped[int] = mapped_column(Integer)
    requested_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(15), default="open")  # open|fulfilled|partial|rejected


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    client_op_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)  # idempotency key
    actor: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(36))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class TransferSuggestion(Base):
    """AI-proposed inter-bank stock transfer. Never executed without a recorded human decision."""
    __tablename__ = "transfer_suggestions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    from_bank_id: Mapped[str] = mapped_column(ForeignKey("blood_banks.id"))
    to_bank_id: Mapped[str] = mapped_column(ForeignKey("blood_banks.id"), index=True)
    blood_group: Mapped[str] = mapped_column(String(3))
    units: Mapped[int] = mapped_column(Integer)
    rationale: Mapped[str] = mapped_column(Text)  # JSON: forecasts, stock, model, accuracy
    model: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(10), default="proposed")  # proposed | approved | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    decided_by: Mapped[str | None] = mapped_column(String(60), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(15))  # admin | coordinator | manager | clinic | hospital
    bank_id: Mapped[str | None] = mapped_column(ForeignKey("blood_banks.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
'''

FILES["app/schemas.py"] = r'''from pydantic import BaseModel, Field


class CollectIn(BaseModel):
    client_op_id: str
    donor_code: str
    bank_id: str
    component: str = "whole_blood"
    unit_code: str | None = None  # facility-assigned, so offline clients can label units before syncing


class ScreenIn(BaseModel):
    client_op_id: str
    results: dict[str, str]  # {"HIV": "negative", "HBV": "negative", ...}


class RequestIn(BaseModel):
    client_op_id: str
    hospital: str
    bank_id: str
    blood_group: str
    units: int = Field(ge=1, le=20)


class DonorIn(BaseModel):
    client_op_id: str
    code: str
    blood_group: str
    birth_year: int = Field(ge=1950, le=2010)
    bank_id: str
'''

FILES["app/services/common.py"] = r'''import json
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
'''

FILES["app/security.py"] = r'''"""Authentication (JWT) and role/facility authorisation."""
import hashlib
import hmac
import os
import secrets
import warnings
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User
from app.services.common import get_db

SECRET = os.getenv("ABBIS_JWT_SECRET")
if not SECRET:
    SECRET = secrets.token_urlsafe(48)
    warnings.warn("ABBIS_JWT_SECRET is not set: using a random secret, so tokens die on restart. "
                  "Set it in production.", stacklevel=1)
ALGO = "HS256"
TOKEN_MINUTES = 60
oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")

_SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, dk = stored.split("$")
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
    except ValueError:
        return False
    return hmac.compare_digest(calc.hex(), dk)


_DUMMY = hash_password("dummy-password-for-constant-time-checks")


def authenticate(db: Session, username: str, password: str):
    user = db.scalar(select(User).where(User.username == username))
    ok = verify_password(password, user.password_hash if user else _DUMMY)  # same cost for unknown users
    return user if (user and ok and user.is_active) else None


def create_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": user.username, "role": user.role, "bank_id": user.bank_id,
                       "iat": now, "exp": now + timedelta(minutes=TOKEN_MINUTES)}, SECRET, algorithm=ALGO)


def current_user(token: str = Depends(oauth2), db: Session = Depends(get_db)) -> User:
    err = HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGO], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        raise err
    user = db.scalar(select(User).where(User.username == payload["sub"]))
    if not user or not user.is_active:   # role comes from the DB, not the token
        raise err
    return user


def require(*roles: str):
    def dep(user: User = Depends(current_user)) -> User:
        if user.role != "admin" and user.role not in roles:
            raise HTTPException(403, f"Role '{user.role}' may not do this")
        return user
    return dep


def enforce_bank(user: User, bank_id: str, also: tuple = ()):
    """Facility-scoped roles may only touch their own blood bank."""
    if user.role in ("admin", "coordinator", *also):
        return
    if user.bank_id != bank_id:
        raise HTTPException(403, "Outside your facility")
'''

FILES["app/main.py"] = r'''import uuid
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
'''

FILES["app/api/transfers.py"] = r'''import json
import math
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.ai.forecast import demand_series, forecast
from app.models import BloodBank, BloodUnit, TransferSuggestion, User
from app.security import enforce_bank, require
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
def suggest(to_bank_id: str, blood_group: str, horizon: int = Query(7, ge=1, le=28), db: Session = Depends(get_db),
            user: User = Depends(require("manager", "coordinator"))):
    enforce_bank(user, to_bank_id)
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
                                 f"{source['demand']} and keeps a {round((SAFETY_MARGIN - 1) * 100)}% margin.")}
    s = TransferSuggestion(from_bank_id=bank.id, to_bank_id=to_bank_id, blood_group=blood_group, units=qty,
                           rationale=json.dumps(rationale), model=target["model"])
    db.add(s)
    db.commit()
    return serialize(s)


@router.get("")
def list_transfers(status: str | None = None, db: Session = Depends(get_db),
                   user: User = Depends(require("manager", "coordinator"))):
    q = select(TransferSuggestion).order_by(TransferSuggestion.created_at.desc())
    if user.role == "manager":
        q = q.where(or_(TransferSuggestion.from_bank_id == user.bank_id, TransferSuggestion.to_bank_id == user.bank_id))
    if status:
        q = q.where(TransferSuggestion.status == status)
    return [serialize(s) for s in db.scalars(q).all()]


@router.post("/{suggestion_id}/decide")
def decide(suggestion_id: str, body: DecisionIn, db: Session = Depends(get_db),
           user: User = Depends(require("manager", "coordinator"))):
    x_actor = user.username
    if (r := replay(db, body.client_op_id)) is not None:
        return r
    s = db.get(TransferSuggestion, suggestion_id)
    if not s:
        raise HTTPException(404, "Unknown suggestion")
    if user.role == "manager" and user.bank_id not in (s.from_bank_id, s.to_bank_id):
        raise HTTPException(403, "Not your suggestion")
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
'''

FILES["client/outbox.py"] = r'''"""Offline-first outbox: queue operations locally, replay them in order when the network returns."""
import json
import sqlite3
import urllib.error
import urllib.request
import uuid


class Outbox:
    def __init__(self, base_url="http://127.0.0.1:8000", db_path="client_outbox.db", token=None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.db = sqlite3.connect(db_path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS outbox (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            op_id TEXT UNIQUE NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',   -- pending | done | rejected
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT)""")
        self.db.commit()

    def enqueue(self, path, body, method="POST"):
        op_id = body.setdefault("client_op_id", uuid.uuid4().hex)
        self.db.execute("INSERT OR IGNORE INTO outbox (op_id, method, path, body) VALUES (?,?,?,?)",
                        (op_id, method, path, json.dumps(body)))
        self.db.commit()
        return op_id

    def pending(self):
        return self.db.execute(
            "SELECT seq, op_id, method, path, body FROM outbox WHERE status='pending' ORDER BY seq").fetchall()

    def summary(self):
        return dict(self.db.execute("SELECT status, COUNT(*) FROM outbox GROUP BY status").fetchall())

    def rejected(self):
        return self.db.execute("SELECT path, last_error FROM outbox WHERE status='rejected' ORDER BY seq").fetchall()

    def _mark(self, seq, status, error=None):
        self.db.execute("UPDATE outbox SET status=?, last_error=?, attempts=attempts+1 WHERE seq=?",
                        (status, error, seq))
        self.db.commit()

    def _send(self, method, path, body):
        req = urllib.request.Request(
            self.base_url + path, data=json.dumps(body).encode(), method=method,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())

    def sync(self):
        """Replay pending ops in order. Stops at the first network/server/auth failure to preserve ordering."""
        stats = {"synced": 0, "rejected": 0, "stopped": None}
        for seq, _op_id, method, path, body in self.pending():
            try:
                self._send(method, path, json.loads(body))
            except urllib.error.HTTPError as e:      # must come before URLError (it is a subclass)
                detail = e.read().decode()[:300]
                if e.code == 401:                    # token expired/invalid: keep the queue, log in again, retry
                    self._mark(seq, "pending", f"401: {detail}")
                    stats["stopped"] = "authentication required"
                    break
                if 400 <= e.code < 500:              # business conflict: needs a human, never auto-retry
                    self._mark(seq, "rejected", f"{e.code}: {detail}")
                    stats["rejected"] += 1
                    continue
                self._mark(seq, "pending", f"{e.code}: {detail}")
                stats["stopped"] = f"server error {e.code}"
                break
            except OSError as e:                     # URLError, connection refused, timeout
                self._mark(seq, "pending", str(e))
                stats["stopped"] = "network unavailable"
                break
            else:
                self._mark(seq, "done")
                stats["synced"] += 1
        return stats
'''

FILES["data/create_users.py"] = r'''"""Create demo users. Passwords are random, printed once, and saved to demo_credentials.json (gitignored)."""
import json
import os
import secrets

from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models import BloodBank, User
from app.security import hash_password


def main():
    Base.metadata.create_all(engine)
    creds = {}
    with SessionLocal() as s:
        mb = s.scalar(select(BloodBank).where(BloodBank.name.like("%Mbarara%")))
        bank_id = mb.id if mb else None
        plan = [("admin", "admin", None), ("coordinator", "coordinator", None),
                ("manager-mbarara", "manager", bank_id), ("clinic-mbarara", "clinic", bank_id),
                ("hospital-a", "hospital", None)]
        for username, role, bid in plan:
            pw = secrets.token_urlsafe(12)
            u = s.scalar(select(User).where(User.username == username))
            if u:
                u.password_hash, u.role, u.bank_id, u.is_active = hash_password(pw), role, bid, True
            else:
                s.add(User(username=username, password_hash=hash_password(pw), role=role, bank_id=bid))
            creds[username] = pw
        s.commit()
    with open("demo_credentials.json", "w") as f:
        json.dump(creds, f, indent=2)
    os.chmod("demo_credentials.json", 0o600)
    print("Demo users (saved to demo_credentials.json, which is gitignored):")
    for k, v in creds.items():
        print(f"  {k:18} {v}")


if __name__ == "__main__":
    main()
'''

FILES["demo/auth.py"] = r'''import json
import urllib.parse
import urllib.request


def login(base, username, creds_file="demo_credentials.json"):
    with open(creds_file) as f:
        password = json.load(f)[username]
    data = urllib.parse.urlencode({"username": username, "password": password}).encode()
    req = urllib.request.Request(base + "/auth/login", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["access_token"]
'''

FILES["demo/offline_demo.py"] = r'''"""A clinic works through an outage, reconnects, and survives a flaky replay."""
import json
import os
import urllib.request
import uuid

from client.outbox import Outbox
from demo.auth import login

BASE = os.getenv("ABBIS_URL", "http://127.0.0.1:8000")
DB = "demo_outbox.db"


def get(path, token):
    req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def main():
    if os.path.exists(DB):
        os.remove(DB)
    token = login(BASE, "clinic-mbarara")
    bank = next(b for b in get("/banks", token) if "Mbarara" in b["name"])
    before = get(f"/inventory?bank_id={bank['id']}", token).get("O+", 0)

    ob = Outbox(BASE, DB, token=token)
    donor = "D-" + uuid.uuid4().hex[:8]
    unit = "U" + uuid.uuid4().hex[:10].upper()
    neg = {t: "negative" for t in ["HIV", "HBV", "HCV", "Syphilis"]}

    print("PHASE 1: clinic is offline, work goes into the local queue")
    ob.enqueue("/donors", {"code": donor, "blood_group": "O+", "birth_year": 2000, "bank_id": bank["id"]})
    ob.enqueue("/units/collect", {"donor_code": donor, "bank_id": bank["id"], "unit_code": unit})
    ob.enqueue(f"/units/{unit}/screen", {"results": neg})
    ob.enqueue("/units/collect", {"donor_code": donor, "bank_id": bank["id"],   # deliberate conflict
                                  "unit_code": "U" + uuid.uuid4().hex[:10].upper()})
    print("  queue:", ob.summary())

    print("PHASE 2: sync attempt while the network is down (dead port)")
    dead = Outbox("http://127.0.0.1:9", DB, token=token)
    print("  result:", dead.sync(), "| queue:", ob.summary())

    print("PHASE 3: connection restored, but the saved login has expired")
    stale = Outbox(BASE, DB, token="expired-or-invalid")
    print("  result:", stale.sync(), "| queue:", ob.summary(), "(nothing lost)")

    print("PHASE 4: staff log in again, replay in order")
    print("  result:", ob.sync(), "| queue:", ob.summary())

    print("PHASE 5: flaky link, the client thinks nothing arrived and replays everything")
    ob.db.execute("UPDATE outbox SET status='pending' WHERE status='done'")
    ob.db.commit()
    print("  result:", ob.sync())

    after = get(f"/inventory?bank_id={bank['id']}", token).get("O+", 0)
    trace = get(f"/units/{unit}/trace", token)
    events = [(e["action"], e["actor"]) for e in trace["events"]]
    print("\nVERIFY")
    print("  O+ inventory:", before, "->", after)
    print("  unit status :", trace["unit"]["status"], "| audit trail:", events)
    print("  needs human review:")
    for path, err in ob.rejected():
        print("   -", path, "->", err)
    assert after == before + 1, "duplicate or lost unit!"
    assert [a for a, _ in events] == ["collect", "screen"], events
    assert all(who == "clinic-mbarara" for _, who in events)
    print("\nOK: no duplicates, no losses, conflict surfaced, every action attributed to a real identity.")


if __name__ == "__main__":
    main()
'''

FILES["demo/transfer_demo.py"] = r'''"""AI suggests a transfer, a manager approves, stock moves, and a repeat approval is refused."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from demo.auth import login

BASE = os.getenv("ABBIS_URL", "http://127.0.0.1:8000")
GROUP = "A+"


def call(method, path, token, body=None, **params):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def main():
    mgr, coord = login(BASE, "manager-mbarara"), login(BASE, "coordinator")
    banks = call("GET", "/banks", coord)
    dest = next(b for b in banks if "Mbarara" in b["name"])

    def stock(label):
        row = ", ".join(f"{b['name'].split()[0]} {call('GET', '/inventory', coord, bank_id=b['id']).get(GROUP, 0)}"
                        for b in banks)
        print(f"  {label}: {row}")

    print(f"STEP 1: stock of {GROUP} (national coordinator's view)")
    stock("before")

    print("\nSTEP 2: the Mbarara manager asks for a forecast-based proposal (nothing moves yet)")
    s = call("POST", "/transfers/suggest", mgr, to_bank_id=dest["id"], blood_group=GROUP)
    if "id" not in s:
        raise SystemExit(f"  No suggestion: {s.get('message')}. Run: python -m data.make_shortage")
    r = s["rationale"]
    print(f"  proposal: move {s['units']} {GROUP} units to {dest['name']}  "
          f"[model: {s['model']}, backtest WAPE {r['destination']['wape']}]")
    print(f"  why: {r['explanation']}")
    print(f"  status: {s['status']}, requires_human_approval: {s['requires_human_approval']}")

    print("\nSTEP 3: the manager approves")
    out = call("POST", f"/transfers/{s['id']}/decide", mgr, {"client_op_id": "demo-" + s["id"], "decision": "approve"})
    print(f"  {out['status']}, {len(out['moved_units'])} units moved")

    print("\nSTEP 4: stock after")
    stock("after")

    print("\nSTEP 5: approving the same suggestion again is refused")
    try:
        call("POST", f"/transfers/{s['id']}/decide", mgr, {"client_op_id": "demo-again", "decision": "approve"})
    except urllib.error.HTTPError as e:
        print(f"  {e.code}: {json.loads(e.read())['detail']}")

    print("\nSTEP 6: a clinic user cannot see or approve transfers")
    clinic = login(BASE, "clinic-mbarara")
    try:
        call("GET", "/transfers", clinic)
    except urllib.error.HTTPError as e:
        print(f"  {e.code}: {json.loads(e.read())['detail']}")


if __name__ == "__main__":
    main()
'''

FILES["tests/conftest.py"] = r'''import os
os.environ["DATABASE_URL"] = "sqlite:///test_abbis.db"
os.environ["ABBIS_JWT_SECRET"] = "test-secret-not-for-production-0123456789"

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import BloodBank, Donor, User
from app.security import hash_password

PW = "pw-12345678"


def make_user(s, username, role, bank_id=None):
    s.add(User(username=username, password_hash=hash_password(PW), role=role, bank_id=bank_id))


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        make_user(s, "admin", "admin")
        s.commit()
    with TestClient(app) as c:
        token = c.post("/auth/login", data={"username": "admin", "password": PW}).json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {token}"})   # default identity: admin
        yield c


@pytest.fixture()
def admin_token(client):
    return client.headers["Authorization"].split()[1]


@pytest.fixture()
def login_as(client):
    """Create a user and return auth headers for them (per-request headers override the admin default)."""
    def _login(username, role, bank_id=None):
        with SessionLocal() as s:
            make_user(s, username, role, bank_id)
            s.commit()
        r = client.post("/auth/login", data={"username": username, "password": PW})
        return {"Authorization": f"Bearer {r.json()['access_token']}"}
    return _login


@pytest.fixture()
def seed(client):
    with SessionLocal() as s:
        bank = BloodBank(name="Test Bank", region="Western", country="Uganda")
        s.add(bank)
        s.flush()
        bank_id = bank.id
        s.add(Donor(code="D1", blood_group="O+", birth_year=1999, bank_id=bank_id))
        s.commit()
    return {"bank_id": bank_id, "donor_code": "D1"}
'''

FILES["tests/test_outbox.py"] = r'''import json
import threading
import time
import urllib.request

import pytest
import uvicorn

from app.main import app
from client.outbox import Outbox

NEG = {t: "negative" for t in ["HIV", "HBV", "HCV", "Syphilis"]}
URL = "http://127.0.0.1:8765"


@pytest.fixture()
def live_server(seed):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    yield URL
    server.should_exit = True
    t.join(timeout=5)


def inventory(bank_id, token):
    req = urllib.request.Request(f"{URL}/inventory?bank_id={bank_id}", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def test_replay_creates_no_duplicates(live_server, seed, tmp_path, admin_token):
    ob = Outbox(live_server, str(tmp_path / "o.db"), token=admin_token)
    ob.enqueue("/units/collect", {"donor_code": "D1", "bank_id": seed["bank_id"], "unit_code": "UOFFLINE1"})
    ob.enqueue("/units/UOFFLINE1/screen", {"results": NEG})
    assert ob.sync()["synced"] == 2
    ob.db.execute("UPDATE outbox SET status='pending'")   # simulate a lost acknowledgement
    ob.db.commit()
    assert ob.sync()["synced"] == 2
    assert inventory(seed["bank_id"], admin_token) == {"O+": 1}


def test_network_failure_keeps_queue_intact(tmp_path):
    ob = Outbox("http://127.0.0.1:9", str(tmp_path / "o.db"), token="x")
    ob.enqueue("/units/collect", {"donor_code": "D1", "bank_id": "x"})
    assert ob.sync()["stopped"] == "network unavailable"
    assert ob.summary() == {"pending": 1}


def test_conflict_is_rejected_not_retried(live_server, seed, tmp_path, admin_token):
    ob = Outbox(live_server, str(tmp_path / "o.db"), token=admin_token)
    ob.enqueue("/units/collect", {"donor_code": "D1", "bank_id": seed["bank_id"], "unit_code": "UA"})
    ob.enqueue("/units/collect", {"donor_code": "D1", "bank_id": seed["bank_id"], "unit_code": "UB"})
    stats = ob.sync()
    assert stats["synced"] == 1 and stats["rejected"] == 1
    assert ob.summary() == {"done": 1, "rejected": 1}
    assert ob.sync() == {"synced": 0, "rejected": 0, "stopped": None}   # nothing retried


def test_bad_token_pauses_sync_without_losing_work(live_server, seed, tmp_path, admin_token):
    ob = Outbox(live_server, str(tmp_path / "o.db"), token="garbage")
    ob.enqueue("/units/collect", {"donor_code": "D1", "bank_id": seed["bank_id"], "unit_code": "UTOK1"})
    assert ob.sync()["stopped"] == "authentication required"
    assert ob.summary() == {"pending": 1}
    ob.token = admin_token                         # staff log in again
    assert ob.sync()["synced"] == 1
'''

FILES["tests/test_auth.py"] = r'''import time

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import BloodBank, User
from app.security import ALGO, SECRET


def collect_body(seed, op="a1", bank_id=None):
    return {"client_op_id": op, "donor_code": seed["donor_code"], "bank_id": bank_id or seed["bank_id"]}


def test_login_and_me(client, login_as):
    assert client.post("/auth/login", data={"username": "admin", "password": "wrong"}).status_code == 401
    h = login_as("clinic1", "clinic")
    me = client.get("/auth/me", headers=h).json()
    assert me["username"] == "clinic1" and me["role"] == "clinic"


def test_no_or_bad_token_rejected(client):
    anon = TestClient(app)
    assert anon.get("/health").status_code == 200
    assert anon.get("/banks").status_code == 401
    assert anon.get("/banks", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_expired_and_forged_tokens_rejected(client):
    expired = jwt.encode({"sub": "admin", "exp": int(time.time()) - 10}, SECRET, algorithm=ALGO)
    forged = jwt.encode({"sub": "admin", "exp": int(time.time()) + 600}, "some-other-secret-key-0123456789ab", algorithm=ALGO)
    for tok in (expired, forged):
        assert client.get("/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_role_permissions(client, seed, login_as):
    clinic = login_as("c1", "clinic", seed["bank_id"])
    hospital = login_as("h1", "hospital")
    req = {"client_op_id": "x1", "hospital": "H", "bank_id": seed["bank_id"], "blood_group": "O+", "units": 1}
    assert client.post("/requests", json=req, headers=clinic).status_code == 403
    assert client.post("/units/collect", json=collect_body(seed), headers=hospital).status_code == 403
    assert client.get("/transfers", headers=clinic).status_code == 403
    assert client.get("/inventory", params={"bank_id": seed["bank_id"]}, headers=hospital).status_code == 200
    assert client.post("/requests", json=req, headers=hospital).status_code == 200


def test_clinic_cannot_touch_another_bank(client, seed, login_as):
    with SessionLocal() as s:
        other = BloodBank(name="Other", region="East", country="Uganda")
        s.add(other)
        s.flush()
        other_id = other.id
        s.commit()
    clinic = login_as("c2", "clinic", seed["bank_id"])
    assert client.post("/units/collect", json=collect_body(seed, bank_id=other_id), headers=clinic).status_code == 403
    assert client.get("/inventory", params={"bank_id": other_id}, headers=clinic).status_code == 403


def test_lockout_after_repeated_failures(client, login_as):
    login_as("victim", "clinic")
    for _ in range(5):
        assert client.post("/auth/login", data={"username": "victim", "password": "nope"}).status_code == 401
    r = client.post("/auth/login", data={"username": "victim", "password": "pw-12345678"})
    assert r.status_code == 429


def test_deactivated_user_loses_access_immediately(client, login_as):
    h = login_as("temp", "clinic")
    assert client.get("/auth/me", headers=h).status_code == 200
    with SessionLocal() as s:
        s.scalar(select(User).where(User.username == "temp")).is_active = False
        s.commit()
    assert client.get("/auth/me", headers=h).status_code == 401


def test_audit_trail_records_authenticated_identity(client, seed, login_as):
    clinic = login_as("c3", "clinic", seed["bank_id"])
    code = client.post("/units/collect", json=collect_body(seed), headers=clinic).json()["unit_code"]
    events = client.get(f"/units/{code}/trace", headers=clinic).json()["events"]
    assert events[0]["actor"] == "c3"
'''

FILES["tests/test_dashboard.py"] = r'''def test_dashboard_served_with_strict_csp(client):
    r = client.get("/app/")
    assert r.status_code == 200 and "ABBIS-Lite" in r.text
    assert "<script>" not in r.text                      # no inline scripts, so the strict CSP works
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/app/app.js").status_code == 200
    assert client.get("/app/style.css").status_code == 200


def test_root_redirects_to_dashboard(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/app/"
'''


def write_all():
    for rel, content in FILES.items():
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            b = BACKUP / rel
            b.parent.mkdir(parents=True, exist_ok=True)
            if not b.exists():                      # keep the first original only
                shutil.copy2(p, b)
        p.write_text(content)
        print(f"  wrote {rel}")
    for pkg in ("app", "app/api", "app/services", "app/models", "client", "demo", "data", "tests"):
        init = ROOT / pkg / "__init__.py"
        if not init.exists():
            init.parent.mkdir(parents=True, exist_ok=True)
            init.write_text("")


def patch_generator():
    g = ROOT / "data" / "generate_synthetic.py"
    if not g.exists():
        print("  (data/generate_synthetic.py not found: run `python -m data.create_users` after generating data)")
        return
    s = g.read_text()
    if "create_users" in s:
        print("  generator already seeds users")
        return
    guard = 'if __name__ == "__main__":\n    main()\n'
    if guard not in s:
        print("  WARNING: could not patch the generator. After generating data, run: python -m data.create_users")
        return
    g.write_text(s.replace(guard, guard + "    from data.create_users import main as seed_users\n    seed_users()\n"))
    print("  generator now seeds demo users")


def update_gitignore():
    gi = ROOT / ".gitignore"
    text = gi.read_text() if gi.exists() else ""
    for line in ("demo_credentials.json", ".apply_jwt_backup/", "demo_outbox.db", "client_outbox.db", "server.log"):
        if line not in text.split():
            text += ("" if text.endswith("\n") or not text else "\n") + line + "\n"
    gi.write_text(text)


def update_docs():
    import re
    a = ROOT / "docs" / "architecture.md"
    if a.exists():
        t = a.read_text()
        new = ("## 7. Security\n"
               "Implemented: JWT authentication (60-minute tokens), scrypt password hashing, role-based access control "
               "(admin / coordinator / manager / clinic / hospital), per-facility scoping, login lockout after 5 failures "
               "(15 min), role re-read from the database on every request so deactivation is immediate, audit trail "
               "carrying the authenticated identity, strict Content-Security-Policy on the dashboard, input validation, "
               "pseudonymous donor codes, synthetic data only.\n"
               "Planned: TLS termination, encryption at rest, per-IP rate limiting, refresh tokens, "
               "two-bank confirmation for transfers, secrets management.\n"
               "**Known trade-off:** per-username lockout can be abused to lock out a user; per-IP limits are planned.\n\n## 8.")
        t2 = re.sub(r"## 7\. Security\n.*?\n## 8\.", lambda m: new, t, count=1, flags=re.S)
        if t2 != t:
            a.write_text(t2)
            print("  updated docs/architecture.md security section")
    r = ROOT / "README.md"
    if r.exists():
        t = r.read_text()
        t = t.replace("- Authentication is not implemented yet; the actor comes from an `X-Actor` header placeholder.\n",
                      "- Authentication is JWT with roles. TLS, refresh tokens and per-IP rate limiting are not implemented yet.\n")
        t = t.replace("JWT + role-based access · ", "")
        r.write_text(t)


if __name__ == "__main__":
    print("Applying JWT + roles (originals backed up to .apply_jwt_backup/):")
    write_all()
    patch_generator()
    update_gitignore()
    update_docs()
    print("\nDone. Next:  python -m pytest -q")
