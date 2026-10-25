"""Authentication (JWT) and role/facility authorisation."""
import hashlib
import hmac
import os
import secrets
import warnings
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User
from app.services.common import get_db

SECRET = os.getenv("ABBIS_JWT_SECRET")
if not SECRET:
    SECRET = secrets.token_urlsafe(48)
    warnings.warn("ABBIS_JWT_SECRET is not set: using a random secret, so tokens die on restart. "
                  "Set it in production.", stacklevel=1)
ALGO = "HS256"
TOKEN_MINUTES = 60
oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")

_SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, dk = stored.split("$")
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
    except ValueError:
        return False
    return hmac.compare_digest(calc.hex(), dk)


_DUMMY = hash_password("dummy-password-for-constant-time-checks")


def authenticate(db: Session, username: str, password: str):
    user = db.scalar(select(User).where(User.username == username))
    ok = verify_password(password, user.password_hash if user else _DUMMY)  # same cost for unknown users
    return user if (user and ok and user.is_active) else None


def create_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": user.username, "role": user.role, "bank_id": user.bank_id,
                       "iat": now, "exp": now + timedelta(minutes=TOKEN_MINUTES)}, SECRET, algorithm=ALGO)


def current_user(token: str = Depends(oauth2), db: Session = Depends(get_db)) -> User:
    err = HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGO], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        raise err
    user = db.scalar(select(User).where(User.username == payload["sub"]))
    if not user or not user.is_active:   # role comes from the DB, not the token
        raise err
    return user


def require(*roles: str):
    def dep(user: User = Depends(current_user)) -> User:
        if user.role != "admin" and user.role not in roles:
            raise HTTPException(403, f"Role '{user.role}' may not do this")
        return user
    return dep


def enforce_bank(user: User, bank_id: str, also: tuple = ()):
    """Facility-scoped roles may only touch their own blood bank."""
    if user.role in ("admin", "coordinator", *also):
        return
    if user.bank_id != bank_id:
        raise HTTPException(403, "Outside your facility")
