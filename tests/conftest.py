import os
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
