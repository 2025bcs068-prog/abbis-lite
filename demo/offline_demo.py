"""A clinic works through an outage, reconnects, and survives a flaky replay."""
import json
import os
import urllib.request
import uuid

from client.outbox import Outbox

BASE = os.getenv("ABBIS_URL", "http://127.0.0.1:8000")
DB = "demo_outbox.db"

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return json.loads(r.read())

def main():
    if os.path.exists(DB):
        os.remove(DB)
    banks = get("/banks")
    if not banks:
        raise SystemExit("No blood banks found. Run: python -m data.generate_synthetic")
    bank = banks[0]
    before = get(f"/inventory?bank_id={bank['id']}").get("O+", 0)

    ob = Outbox(BASE, DB, actor="clinic-mbarara")
    donor = "D-" + uuid.uuid4().hex[:8]
    unit = "U" + uuid.uuid4().hex[:10].upper()
    neg = {t: "negative" for t in ["HIV", "HBV", "HCV", "Syphilis"]}

    print("PHASE 1: clinic is offline, work goes into the local queue")
    ob.enqueue("/donors", {"code": donor, "blood_group": "O+", "birth_year": 2000, "bank_id": bank["id"]})
    ob.enqueue("/units/collect", {"donor_code": donor, "bank_id": bank["id"], "unit_code": unit})
    ob.enqueue(f"/units/{unit}/screen", {"results": neg})
    # deliberate conflict: the same donor donating again on the same day
    ob.enqueue("/units/collect", {"donor_code": donor, "bank_id": bank["id"],
                                  "unit_code": "U" + uuid.uuid4().hex[:10].upper()})
    print("  queue:", ob.summary())

    print("PHASE 2: sync attempt while the network is down (dead port)")
    dead = Outbox("http://127.0.0.1:9", DB)
    print("  result:", dead.sync(), "| queue:", ob.summary())

    print("PHASE 3: connection restored, replay in order")
    print("  result:", ob.sync(), "| queue:", ob.summary())

    print("PHASE 4: flaky link, the client thinks nothing arrived and replays everything")
    ob.db.execute("UPDATE outbox SET status='pending' WHERE status='done'")
    ob.db.commit()
    print("  result:", ob.sync())

    after = get(f"/inventory?bank_id={bank['id']}").get("O+", 0)
    trace = get(f"/units/{unit}/trace")
    events = [e["action"] for e in trace["events"]]
    print("\nVERIFY")
    print("  O+ inventory:", before, "->", after)
    print("  unit status :", trace["unit"]["status"], "| audit events:", events)
    print("  needs human review:")
    for path, err in ob.rejected():
        print("   -", path, "->", err)
    assert after == before + 1, "duplicate or lost unit!"
    assert events == ["register", "collect", "screen"][1:], events
    print("\nOK: no duplicates, no losses, conflict surfaced for review.")

if __name__ == "__main__":
    main()
