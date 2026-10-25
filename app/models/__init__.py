import uuid
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
