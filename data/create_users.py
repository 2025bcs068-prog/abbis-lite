"""Create demo users. Passwords are random, printed once, and saved to demo_credentials.json (gitignored)."""
import json
import os
import secrets

from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models import BloodBank, User
from app.security import hash_password


def main():
    Base.metadata.create_all(engine)
    creds = {}
    with SessionLocal() as s:
        mb = s.scalar(select(BloodBank).where(BloodBank.name.like("%Mbarara%")))
        bank_id = mb.id if mb else None
        plan = [("admin", "admin", None), ("coordinator", "coordinator", None),
                ("manager-mbarara", "manager", bank_id), ("clinic-mbarara", "clinic", bank_id),
                ("hospital-a", "hospital", None)]
        for username, role, bid in plan:
            pw = secrets.token_urlsafe(12)
            u = s.scalar(select(User).where(User.username == username))
            if u:
                u.password_hash, u.role, u.bank_id, u.is_active = hash_password(pw), role, bid, True
            else:
                s.add(User(username=username, password_hash=hash_password(pw), role=role, bank_id=bid))
            creds[username] = pw
        s.commit()
    with open("demo_credentials.json", "w") as f:
        json.dump(creds, f, indent=2)
    os.chmod("demo_credentials.json", 0o600)
    print("Demo users (saved to demo_credentials.json, which is gitignored):")
    for k, v in creds.items():
        print(f"  {k:18} {v}")


if __name__ == "__main__":
    main()
