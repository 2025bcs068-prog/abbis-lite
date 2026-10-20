import os
os.environ["DATABASE_URL"] = "sqlite:///test_abbis.db"

import pytest
from fastapi.testclient import TestClient
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import BloodBank, Donor

@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c

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
