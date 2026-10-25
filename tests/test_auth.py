import time

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
