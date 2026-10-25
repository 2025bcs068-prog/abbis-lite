"""A clinic works through an outage, reconnects, and survives a flaky replay."""
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
