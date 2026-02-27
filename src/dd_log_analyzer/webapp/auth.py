"""Authentication — Basic auth with JWT session tokens."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

_bearer = HTTPBearer(auto_error=False)

SECRET_KEY = os.getenv("WEB_SECRET_KEY", secrets.token_hex(32))
USERNAME = os.getenv("WEB_USERNAME", "admin")
_PASSWORD = os.getenv("WEB_PASSWORD", "changeme")
TOKEN_EXPIRE_HOURS = 24

# Use SHA-256 HMAC for password verification (simple, no C deps)
_PASSWORD_HASH = hmac.new(SECRET_KEY.encode(), _PASSWORD.encode(), hashlib.sha256).hexdigest()


def verify_password(plain: str) -> bool:
    candidate = hmac.new(SECRET_KEY.encode(), plain.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(candidate, _PASSWORD_HASH)


def create_token(username: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode({"sub": username, "exp": exp}, SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload.get("sub")
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


async def require_auth(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    username = decode_token(creds.credentials)
    if username is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return username
