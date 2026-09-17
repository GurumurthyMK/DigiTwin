"""Auth dependencies: resolve current user from Bearer header OR httpOnly cookie.

Cookie-authed state-changing requests additionally pass an Origin/Referer
allowlist check (CSRF defense-in-depth behind SameSite cookies).
"""

from urllib.parse import urlparse

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from starlette import status

from app.core import security
from app.core.config import get_settings
from app.core.errors import AppError
from app.db import models
from app.db.session import get_db

_bearer = HTTPBearer(auto_error=False)
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True  # non-browser / test clients; SameSite=Lax remains the backstop
    try:
        o = urlparse(origin)
    except ValueError:
        return False
    base = urlparse(str(request.base_url))
    if (o.scheme, o.netloc) == (base.scheme, base.netloc):
        return True  # same-origin (incl. same-origin dev proxy)
    allowed = get_settings().cors_origin_list
    return any((o.scheme, o.netloc) == (urlparse(a).scheme, urlparse(a).netloc) for a in allowed)


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> models.User:
    s = get_settings()
    token = creds.credentials if creds else None
    via_cookie = False
    if not token:
        token = request.cookies.get(security.ACCESS_COOKIE)
        via_cookie = token is not None
    if not token:
        raise AppError("not_authenticated", "Not authenticated.", status.HTTP_401_UNAUTHORIZED)
    user_id = security.decode_access_token(token, s.jwt_secret_key, s.jwt_algorithm)
    if not user_id:
        raise AppError("invalid_token", "Invalid or expired token.", status.HTTP_401_UNAUTHORIZED)
    if via_cookie and request.method not in _SAFE_METHODS and not _origin_allowed(request):
        raise AppError("csrf_blocked", "Cross-site request refused.", status.HTTP_403_FORBIDDEN)
    user = db.get(models.User, user_id)
    if not user or not user.is_active:
        raise AppError("invalid_token", "Invalid or expired token.", status.HTTP_401_UNAUTHORIZED)
    return user
