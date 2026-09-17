"""Password hashing + JWT access/refresh token handling.

Access tokens: short-lived JWTs (stateless).
Refresh tokens: opaque random strings, SHA-256 hashed at rest in DB,
rotated on every refresh, revocable on logout.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str, secret: str, algorithm: str, expires_minutes: int) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=expires_minutes)
    return jwt.encode(
        {"sub": subject, "exp": expire, "type": "access"}, secret, algorithm=algorithm
    )


def decode_access_token(token: str, secret: str, algorithm: str) -> str | None:
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
        if payload.get("type") != "access":
            return None
        return payload.get("sub")
    except JWTError:
        return None


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# Dual-transport cookie names. Web uses httpOnly cookies (no JS token storage);
# mobile keeps Authorization headers + secure storage. Backend accepts both.
ACCESS_COOKIE = "digitwin_access"
REFRESH_COOKIE = "digitwin_refresh"


def set_auth_cookies(response, access: str, refresh: str, settings) -> None:
    response.set_cookie(
        ACCESS_COOKIE,
        access,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        samesite=settings.cookie_samesite,
        secure=settings.cookie_secure,
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=settings.refresh_token_expire_days * 86400,
        httponly=True,
        samesite=settings.cookie_samesite,
        secure=settings.cookie_secure,
        path="/",
    )


def clear_auth_cookies(response, settings) -> None:
    for name in (ACCESS_COOKIE, REFRESH_COOKIE):
        response.delete_cookie(
            name, path="/", samesite=settings.cookie_samesite, secure=settings.cookie_secure
        )
