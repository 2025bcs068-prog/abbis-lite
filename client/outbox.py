"""Offline-first outbox: queue operations locally, replay them in order when the network returns."""
import json
import sqlite3
import urllib.error
import urllib.request
import uuid


class Outbox:
    def __init__(self, base_url="http://127.0.0.1:8000", db_path="client_outbox.db", actor="clinic-1"):
        self.base_url = base_url.rstrip("/")
        self.actor = actor
        self.db = sqlite3.connect(db_path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS outbox (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            op_id TEXT UNIQUE NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',   -- pending | done | rejected
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT)""")
        self.db.commit()

    def enqueue(self, path, body, method="POST"):
        op_id = body.setdefault("client_op_id", uuid.uuid4().hex)
        self.db.execute("INSERT OR IGNORE INTO outbox (op_id, method, path, body) VALUES (?,?,?,?)",
                        (op_id, method, path, json.dumps(body)))
        self.db.commit()
        return op_id

    def pending(self):
        return self.db.execute(
            "SELECT seq, op_id, method, path, body FROM outbox WHERE status='pending' ORDER BY seq").fetchall()

    def summary(self):
        return dict(self.db.execute("SELECT status, COUNT(*) FROM outbox GROUP BY status").fetchall())

    def rejected(self):
        return self.db.execute("SELECT path, last_error FROM outbox WHERE status='rejected' ORDER BY seq").fetchall()

    def _mark(self, seq, status, error=None):
        self.db.execute("UPDATE outbox SET status=?, last_error=?, attempts=attempts+1 WHERE seq=?",
                        (status, error, seq))
        self.db.commit()

    def _send(self, method, path, body):
        req = urllib.request.Request(
            self.base_url + path, data=json.dumps(body).encode(), method=method,
            headers={"Content-Type": "application/json", "X-Actor": self.actor})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())

    def sync(self):
        """Replay pending ops in order. Stops at the first network/server failure to preserve ordering."""
        stats = {"synced": 0, "rejected": 0, "stopped": None}
        for seq, _op_id, method, path, body in self.pending():
            try:
                self._send(method, path, json.loads(body))
            except urllib.error.HTTPError as e:      # must come before URLError (it is a subclass)
                detail = e.read().decode()[:300]
                if 400 <= e.code < 500:              # business conflict: needs a human, never auto-retry
                    self._mark(seq, "rejected", f"{e.code}: {detail}")
                    stats["rejected"] += 1
                    continue
                self._mark(seq, "pending", f"{e.code}: {detail}")
                stats["stopped"] = f"server error {e.code}"
                break
            except OSError as e:                     # URLError, connection refused, timeout
                self._mark(seq, "pending", str(e))
                stats["stopped"] = "network unavailable"
                break
            else:
                self._mark(seq, "done")
                stats["synced"] += 1
        return stats
