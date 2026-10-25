import json
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
