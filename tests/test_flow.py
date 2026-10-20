NEG = {t: "negative" for t in ["HIV", "HBV", "HCV", "Syphilis"]}

def collect(client, seed, op="op1"):
    return client.post("/units/collect", json={"client_op_id": op, "donor_code": seed["donor_code"],
                                               "bank_id": seed["bank_id"]})

def test_full_flow(client, seed):
    code = collect(client, seed).json()["unit_code"]
    r = client.post(f"/units/{code}/screen", json={"client_op_id": "op2", "results": NEG})
    assert r.json()["status"] == "available"
    assert client.get("/inventory", params={"bank_id": seed["bank_id"]}).json() == {"O+": 1}
    r = client.post("/requests", json={"client_op_id": "op3", "hospital": "H1",
                                       "bank_id": seed["bank_id"], "blood_group": "O+", "units": 2})
    body = r.json()
    assert body["status"] == "partial" and body["issued_units"] == [code]
    trace = client.get(f"/units/{code}/trace").json()
    assert trace["unit"]["status"] == "issued"
    assert [e["action"] for e in trace["events"]] == ["collect", "screen", "issue"]

def test_collect_is_idempotent(client, seed):
    a = collect(client, seed).json()
    b = collect(client, seed).json()
    assert a == b

def test_positive_screen_discards_unit(client, seed):
    code = collect(client, seed).json()["unit_code"]
    res = dict(NEG, HBV="positive")
    assert client.post(f"/units/{code}/screen", json={"client_op_id": "s1", "results": res}).json()["status"] == "discarded"
    assert client.get("/inventory", params={"bank_id": seed["bank_id"]}).json() == {}

def test_donor_interval_enforced(client, seed):
    assert collect(client, seed, "a").status_code == 200
    assert collect(client, seed, "b").status_code == 409

def test_incomplete_screening_rejected(client, seed):
    code = collect(client, seed).json()["unit_code"]
    r = client.post(f"/units/{code}/screen", json={"client_op_id": "s2", "results": {"HIV": "negative"}})
    assert r.status_code == 422
