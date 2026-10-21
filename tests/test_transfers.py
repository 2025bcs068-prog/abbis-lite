from datetime import date, timedelta

from app.db import SessionLocal
from app.models import BloodBank, BloodUnit, Donor, TransferSuggestion


def setup(units_in_stock=3, units_proposed=2):
    with SessionLocal() as s:
        a, b = BloodBank(name="A", region="W", country="UG"), BloodBank(name="B", region="E", country="UG")
        s.add_all([a, b]); s.flush()
        d = Donor(code="DX", blood_group="O+", birth_year=1999, bank_id=a.id)
        s.add(d); s.flush()
        for i in range(units_in_stock):
            s.add(BloodUnit(unit_code=f"T{i}", donor_id=d.id, bank_id=a.id, blood_group="O+",
                            collected_on=date.today(), expires_on=date.today() + timedelta(days=30),
                            status="available"))
        sug = TransferSuggestion(from_bank_id=a.id, to_bank_id=b.id, blood_group="O+", units=units_proposed,
                                 rationale="{}", model="holt_winters")
        s.add(sug); s.flush()
        ids = {"a": a.id, "b": b.id, "sid": sug.id}
        s.commit()
    return ids


def inv(client, bank):
    return client.get("/inventory", params={"bank_id": bank}).json()


def test_approve_moves_units_and_is_idempotent(client):
    ids = setup()
    body = {"client_op_id": "d1", "decision": "approve"}
    r = client.post(f"/transfers/{ids['sid']}/decide", json=body).json()
    assert r["status"] == "approved" and len(r["moved_units"]) == 2
    assert inv(client, ids["b"]) == {"O+": 2} and inv(client, ids["a"]) == {"O+": 1}
    assert client.post(f"/transfers/{ids['sid']}/decide", json=body).json() == r            # replay is safe
    assert client.post(f"/transfers/{ids['sid']}/decide",
                       json={"client_op_id": "d2", "decision": "reject", "reason": "x"}).status_code == 409


def test_reject_needs_reason_and_moves_nothing(client):
    ids = setup()
    url = f"/transfers/{ids['sid']}/decide"
    assert client.post(url, json={"client_op_id": "r1", "decision": "reject"}).status_code == 422
    r = client.post(url, json={"client_op_id": "r2", "decision": "reject", "reason": "Road closed"}).json()
    assert r["status"] == "rejected" and inv(client, ids["a"]) == {"O+": 3} and inv(client, ids["b"]) == {}


def test_stale_suggestion_cannot_over_transfer(client):
    ids = setup(units_in_stock=1, units_proposed=2)
    r = client.post(f"/transfers/{ids['sid']}/decide", json={"client_op_id": "s1", "decision": "approve"})
    assert r.status_code == 409 and inv(client, ids["a"]) == {"O+": 1}
