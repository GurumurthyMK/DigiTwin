"""P0-2A: SMTP password-reset delivery — config, construction, transport, safety.

No test here touches a real SMTP server: delivery goes through a fake
transport class or the `email_sender` seam on `request_password_reset`.
"""

import logging
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import email as _email
from app.core.config import Settings
from app.core.email import EmailDeliveryError
from app.db import models
from app.db.session import SessionLocal
from app.main import app
from app.services import auth_service as _as

client = TestClient(app)
PW = "passWord123"


def _register(email: str) -> None:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": PW})
    assert r.status_code in (200, 201), r.text


class FakeSMTP:
    """Stdlib-SMTP stand-in. Records STARTTLS/auth/send calls."""

    instances: ClassVar[list["FakeSMTP"]] = []

    def __init__(self, host: str, port: int, timeout: int = 10):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.starttls_called = False
        self.login_args: tuple[str, str] | None = None
        self.sent = None
        self.quit_called = False
        FakeSMTP.instances.append(self)

    def starttls(self) -> None:
        self.starttls_called = True

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(self, message) -> None:
        self.sent = message

    def quit(self) -> None:
        self.quit_called = True


@pytest.fixture(autouse=True)
def _clear_fake_smtp():
    FakeSMTP.instances.clear()
    yield
    FakeSMTP.instances.clear()
    # Shared test DB: remove reset rows minted here so later modules that
    # assert on a pristine password_reset_tokens table stay deterministic.
    db = SessionLocal()
    try:
        user_ids = select(models.User.id).where(
            models.User.email.in_(
                [
                    "smtpseam@example.com",
                    "smtpdown@example.com",
                    "smtpendpoint@example.com",
                    "smtpsuccess@example.com",
                ]
            )
        )
        db.execute(
            models.PasswordResetToken.__table__.delete().where(
                models.PasswordResetToken.user_id.in_(user_ids)
            )
        )
        db.commit()
    finally:
        db.close()


def _smtp_settings(**overrides) -> Settings:
    base = {
        "email_backend": "smtp",
        "smtp_host": "mail.example.com",
        "smtp_port": 587,
        "smtp_username": "relay-user",
        "smtp_password": "relay-pass",
        "smtp_from": "DigiTwin <no-reply@example.com>",
        "smtp_use_tls": True,
        "app_base_url": "https://app.example.com",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore


# ---- configuration parsing ----


def test_smtp_settings_defaults_are_safe():
    s = Settings()
    assert s.email_backend == "log"
    assert s.smtp_host == ""
    assert s.smtp_configured is False
    assert s.smtp_port == 587
    assert s.smtp_use_tls is True


def test_smtp_configured_requires_backend_and_host():
    assert _smtp_settings().smtp_configured is True
    assert _smtp_settings(email_backend="log").smtp_configured is False
    assert _smtp_settings(smtp_host="").smtp_configured is False
    assert _smtp_settings(smtp_host="  ").smtp_configured is False


# ---- link + message construction ----


def test_build_reset_link_handles_trailing_slash():
    link = _email.build_reset_link("https://app.example.com/", "tok123")
    assert link == "https://app.example.com/reset?token=tok123"
    assert _email.build_reset_link("https://app.example.com", "tok123") == link


def test_password_reset_message_contains_link_and_expiry():
    link = "https://app.example.com/reset?token=abc"
    msg = _email.build_password_reset_message("u@example.com", link, sender="no-reply@example.com")
    assert msg.to == "u@example.com"
    assert msg.sender == "no-reply@example.com"
    assert msg.subject == _email.RESET_SUBJECT
    assert link in msg.text_body
    assert "one hour" in msg.text_body
    assert "single-use" in msg.text_body


# ---- SMTP transport (fake) ----


def test_smtp_delivery_success_uses_tls_auth_and_from():
    s = _smtp_settings()
    _email.send_password_reset("u@example.com", "raw-token-1", settings=s, smtp_client=FakeSMTP)
    assert len(FakeSMTP.instances) == 1
    fake = FakeSMTP.instances[0]
    assert (fake.host, fake.port) == ("mail.example.com", 587)
    assert fake.starttls_called is True
    assert fake.login_args == ("relay-user", "relay-pass")
    assert fake.quit_called is True
    assert fake.sent["To"] == "u@example.com"
    assert fake.sent["From"] == "DigiTwin <no-reply@example.com>"
    assert fake.sent["Subject"] == _email.RESET_SUBJECT
    assert "https://app.example.com/reset?token=raw-token-1" in fake.sent.get_content()


def test_smtp_skips_login_without_username_and_tls_when_disabled():
    s = _smtp_settings(smtp_username="", smtp_password="", smtp_use_tls=False)
    _email.send_password_reset("u@example.com", "raw-token-2", settings=s, smtp_client=FakeSMTP)
    fake = FakeSMTP.instances[0]
    assert fake.starttls_called is False
    assert fake.login_args is None
    assert fake.sent is not None


def test_smtp_transport_failure_raises_without_secrets():
    class _Boom(FakeSMTP):
        def send_message(self, message) -> None:
            raise OSError("relay down")

    s = _smtp_settings()
    with pytest.raises(EmailDeliveryError) as exc:
        _email.send_password_reset("u@example.com", "raw-token-3", settings=s, smtp_client=_Boom)
    assert "raw-token-3" not in str(exc.value)
    assert "relay-pass" not in str(exc.value)
    assert "reset?token" not in str(exc.value)


# ---- dev/test backend: metadata only, never tokens ----


def test_log_backend_never_logs_token_or_link(caplog):
    raw = "super-secret-raw-token-xyz"
    s = Settings(email_backend="log", app_base_url="https://app.example.com")
    with caplog.at_level(logging.INFO, logger="app.core.email"):
        _email.send_password_reset("u@example.com", raw, settings=s)
    assert FakeSMTP.instances == []
    assert raw not in caplog.text
    assert "reset?token" not in caplog.text
    assert "u@example.com" in caplog.text  # recipient metadata is still observable


# ---- service wiring: seam, failure safety, storage ----


def test_request_uses_sender_seam_and_stores_hash_only():
    email = "smtpseam@example.com"
    _register(email)
    captured: list[_email.EmailMessage] = []
    db = SessionLocal()
    try:
        _as.request_password_reset(db, email, email_sender=captured.append)
        raw = captured[0].text_body.split("token=")[1].split()[0]
        rows = db.scalars(
            select(models.PasswordResetToken).where(
                models.PasswordResetToken.token_hash == _hash(raw)
            )
        ).all()
        assert len(rows) == 1
        # Hash-only storage: the raw secret appears nowhere in the row.
        stored = rows[0]
        assert raw not in (stored.token_hash, stored.user_id, str(stored.id))
        assert captured[0].to == email
    finally:
        db.close()


def _hash(raw: str) -> str:
    from app.core import security as _sec

    return _sec.hash_refresh_token(raw)


def test_request_delivery_failure_stays_silent_and_keeps_token(caplog):
    email = "smtpdown@example.com"
    _register(email)
    seen: list[_email.EmailMessage] = []

    def _failing(message) -> None:
        seen.append(message)
        raise EmailDeliveryError("relay down")

    db = SessionLocal()
    try:
        with caplog.at_level(logging.WARNING):
            _as.request_password_reset(db, email, email_sender=_failing)  # must not raise
        row = db.scalar(select(models.PasswordResetToken))
        assert row is not None and row.used_at is None  # retry stays possible
    finally:
        db.close()
    raw = seen[0].text_body.split("token=")[1].split()[0]
    assert raw not in caplog.text
    assert "reset?token" not in caplog.text


def test_endpoint_200_without_token_for_known_and_unknown(monkeypatch):
    known = "smtpendpoint@example.com"
    _register(known)

    def _down(*args, **kwargs) -> None:
        raise EmailDeliveryError("relay down")

    monkeypatch.setattr(_email, "send_password_reset", _down)
    for target in (known, "ghost-smtp-1@example.com"):
        r = client.post("/api/v1/auth/password-reset/request", json={"email": target})
        assert r.status_code == 200, r.text
        body = r.text
        assert "token" not in body.lower()
        assert r.json() == {"ok": True}
    # Known account still got a usable token row despite the outage.
    db = SessionLocal()
    try:
        user = db.scalar(select(models.User).where(models.User.email == known))
        rows = db.scalars(
            select(models.PasswordResetToken).where(models.PasswordResetToken.user_id == user.id)
        ).all()
        assert len(rows) >= 1
    finally:
        db.close()


def test_endpoint_response_never_contains_token_even_on_success():
    email = "smtpsuccess@example.com"
    _register(email)
    r = client.post("/api/v1/auth/password-reset/request", json={"email": email})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert "token" not in r.text.lower()
