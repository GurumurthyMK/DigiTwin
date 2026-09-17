"""Auth routes: thin HTTP layer over auth_service."""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session
from starlette import status

from app.api.deps import get_current_user
from app.core import security
from app.core.config import get_settings
from app.db import models
from app.db.session import get_db
from app.schemas import v1 as s
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _pair_with_cookies(response: Response, access: str, refresh: str) -> s.TokenPair:
    security.set_auth_cookies(response, access, refresh, get_settings())
    return s.TokenPair(access_token=access, refresh_token=refresh)


@router.post("/register", response_model=s.TokenPair, status_code=status.HTTP_201_CREATED)
def register(body: s.RegisterIn, response: Response, db: Session = Depends(get_db)):
    _, access, refresh = auth_service.register(db, body.email, body.password)
    return _pair_with_cookies(response, access, refresh)


@router.post("/login", response_model=s.TokenPair)
def login(body: s.LoginIn, response: Response, db: Session = Depends(get_db)):
    _, access, refresh = auth_service.authenticate(db, body.email, body.password)
    return _pair_with_cookies(response, access, refresh)


@router.post("/refresh", response_model=s.TokenPair)
def refresh(
    request: Request,
    response: Response,
    body: s.RefreshIn | None = None,
    db: Session = Depends(get_db),
):
    raw = (body.refresh_token if body and body.refresh_token else None) or request.cookies.get(
        security.REFRESH_COOKIE
    )
    access, new_refresh = auth_service.refresh(db, raw)
    return _pair_with_cookies(response, access, new_refresh)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    body: s.RefreshIn | None = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    # With a token body: revoke that session. Without: revoke all user sessions
    # so logout can never silently become a client-side-only no-op.
    raw = body.refresh_token if body and body.refresh_token else None
    auth_service.logout(db, raw, user_id=user.id, all_sessions=raw is None)
    security.clear_auth_cookies(response, get_settings())


@router.get("/me", response_model=s.UserOut)
def me(user: models.User = Depends(get_current_user)):
    return user


@router.post("/password-reset/request", status_code=status.HTTP_200_OK)
def password_reset_request(body: s.PasswordResetRequest, db: Session = Depends(get_db)):
    # Always 200: unknown emails must be indistinguishable (no enumeration).
    auth_service.request_password_reset(db, body.email)
    return {"ok": True}


@router.post("/password-reset/confirm", status_code=status.HTTP_200_OK)
def password_reset_confirm(body: s.PasswordResetConfirm, db: Session = Depends(get_db)):
    auth_service.confirm_password_reset(db, body.token, body.new_password)
    return {"ok": True}


@router.post("/password/change", response_model=s.TokenPair)
def password_change(
    body: s.PasswordChange,
    response: Response,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    access, refresh = auth_service.change_password(
        db, user, body.current_password, body.new_password
    )
    return _pair_with_cookies(response, access, refresh)


@router.delete("/users/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_me(
    body: s.AccountDelete,
    response: Response,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    auth_service.delete_account(db, user, body.password)
    security.clear_auth_cookies(response, get_settings())
