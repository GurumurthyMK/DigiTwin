"""Auth application service: register/login/refresh/logout. No HTTP here."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_settings
from app.core.errors import AppError
from app.db import models

# Expired tokens linger this long before purge (lets recently-expired rows
# still produce a clean invalid_refresh instead of vanishing mid-debug).
PURGE_GRACE_DAYS = 1


def _settings():
    return get_settings()


def _revoke_all(db: Session, user_id: str, now: datetime) -> None:
    for t in db.scalars(select(models.RefreshToken).where(models.RefreshToken.user_id == user_id)):
        if t.revoked_at is None:
            t.revoked_at = now


def get_or_create_profile(db: Session, user: models.User) -> models.StudentProfile:
    profile = db.scalar(
        select(models.StudentProfile).where(models.StudentProfile.user_id == user.id)
    )
    if profile is None:
        profile = models.StudentProfile(user_id=user.id)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def register(db: Session, email: str, password: str) -> tuple[models.User, str, str]:
    existing = db.scalar(select(models.User).where(models.User.email == email.lower().strip()))
    if existing:
        raise AppError("email_taken", "Email already registered.", 409)
    user = models.User(email=email.lower().strip(), password_hash=security.hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    get_or_create_profile(db, user)
    return user, *issue_tokens(db, user)


def authenticate(db: Session, email: str, password: str) -> tuple[models.User, str, str]:
    user = db.scalar(select(models.User).where(models.User.email == email.lower().strip()))
    if not user or not user.is_active or not security.verify_password(password, user.password_hash):
        raise AppError("invalid_credentials", "Invalid email or password.", 401)
    get_or_create_profile(db, user)
    return user, *issue_tokens(db, user)


def issue_tokens(db: Session, user: models.User) -> tuple[str, str]:
    s = _settings()
    access = security.create_access_token(
        user.id, s.jwt_secret_key, s.jwt_algorithm, s.access_token_expire_minutes
    )
    raw_refresh = security.new_refresh_token()
    db.add(
        models.RefreshToken(
            user_id=user.id,
            token_hash=security.hash_refresh_token(raw_refresh),
            expires_at=datetime.now(UTC) + timedelta(days=s.refresh_token_expire_days),
            created_at=datetime.now(UTC),
        )
    )
    db.commit()
    return access, raw_refresh


def refresh(db: Session, raw_token: str | None) -> tuple[str, str]:
    if not raw_token:
        raise AppError("invalid_refresh", "Refresh token invalid or expired.", 401)
    tok = db.scalar(
        select(models.RefreshToken).where(
            models.RefreshToken.token_hash == security.hash_refresh_token(raw_token)
        )
    )
    now = datetime.now(UTC)
    exp = tok.expires_at if tok else None
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    if not tok or (exp is not None and exp <= now):
        raise AppError("invalid_refresh", "Refresh token invalid or expired.", 401)
    if tok.revoked_at is not None:
        # Reuse of a rotated/revoked token signals theft: kill every session.
        _revoke_all(db, tok.user_id, now)
        db.commit()
        raise AppError("refresh_reused", "Refresh token reused; all sessions revoked.", 401)
    tok.revoked_at = now  # rotate
    db.commit()
    user = db.get(models.User, tok.user_id)
    if not user or not user.is_active:
        raise AppError("invalid_refresh", "Refresh token invalid or expired.", 401)
    access, new_refresh = issue_tokens(db, user)
    purge_expired_tokens(db)  # opportunistic cleanup, no cron needed at this scale
    return access, new_refresh


def logout(
    db: Session, raw_token: str | None, user_id: str | None = None, all_sessions: bool = False
) -> None:
    now = datetime.now(UTC)
    if all_sessions and user_id:
        _revoke_all(db, user_id, now)
        db.commit()
        return
    if raw_token:
        tok = db.scalar(
            select(models.RefreshToken).where(
                models.RefreshToken.token_hash == security.hash_refresh_token(raw_token)
            )
        )
        if tok and tok.revoked_at is None:
            tok.revoked_at = now
            db.commit()


def purge_expired_tokens(db: Session) -> int:
    """Delete refresh tokens expired beyond grace. Unexpired revoked rows are
    KEPT — reuse detection depends on them. Returns rows removed."""
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=PURGE_GRACE_DAYS)
    result = db.execute(delete(models.RefreshToken).where(models.RefreshToken.expires_at < cutoff))
    db.commit()
    return result.rowcount


RESET_TTL_HOURS = 1


def request_password_reset(db: Session, email: str) -> None:
    """Always succeeds silently: unknown emails produce no row and no signal."""
    from app.core import email as _email

    user = db.scalar(select(models.User).where(models.User.email == email.lower().strip()))
    if not user:
        return
    now = datetime.now(UTC)
    for stale in db.scalars(
        select(models.PasswordResetToken).where(
            models.PasswordResetToken.user_id == user.id,
            models.PasswordResetToken.used_at.is_(None),
        )
    ):
        stale.used_at = now  # superseded: only the newest link ever works
    raw = security.new_refresh_token()
    db.add(
        models.PasswordResetToken(
            user_id=user.id,
            token_hash=security.hash_refresh_token(raw),
            expires_at=now + timedelta(hours=RESET_TTL_HOURS),
            created_at=now,
        )
    )
    db.commit()
    _email.send(
        user.email,
        "DigiTwin password reset",
        f"Use this one-hour link to reset your password: /reset?token={raw}",
        sensitive=True,
    )


def confirm_password_reset(db: Session, raw_token: str, new_password: str) -> None:
    if not raw_token:
        raise AppError("invalid_reset", "Reset link invalid or expired.", 400)
    tok = db.scalar(
        select(models.PasswordResetToken).where(
            models.PasswordResetToken.token_hash == security.hash_refresh_token(raw_token)
        )
    )
    now = datetime.now(UTC)
    exp = tok.expires_at if tok else None
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    if not tok or tok.used_at is not None or (exp is not None and exp <= now):
        raise AppError("invalid_reset", "Reset link invalid or expired.", 400)
    user = db.get(models.User, tok.user_id)
    if not user or not user.is_active:
        raise AppError("invalid_reset", "Reset link invalid or expired.", 400)
    tok.used_at = now
    user.password_hash = security.hash_password(new_password)
    _revoke_all(db, user.id, now)  # fresh start on every session
    db.commit()


def change_password(
    db: Session, user: models.User, current_password: str, new_password: str
) -> tuple[str, str]:
    if not security.verify_password(current_password, user.password_hash):
        raise AppError("wrong_password", "Current password is incorrect.", 400)
    user.password_hash = security.hash_password(new_password)
    _revoke_all(db, user.id, datetime.now(UTC))
    db.commit()
    return issue_tokens(db, user)


def delete_account(db: Session, user: models.User, password: str) -> None:
    """Password-confirmed self-deletion. FK cascades wipe all student state."""
    if not security.verify_password(password, user.password_hash):
        raise AppError("wrong_password", "Password is incorrect.", 400)
    db.delete(user)
    db.commit()
