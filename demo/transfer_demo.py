"""AI suggests a transfer, a manager approves, stock moves, and a repeat approval is refused."""
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
