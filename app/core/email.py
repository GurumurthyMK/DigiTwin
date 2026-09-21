"""Password-reset email delivery boundary (P0-2A).

Auth code depends only on `send_password_reset(to, raw_token)` — never on
`smtplib` directly. Two backends, selected by `settings.email_backend`:

- "log" (dev/test default): records to/subject metadata only. The reset
  token and link are NEVER written to logs, even in dev.
- "smtp": delivers through the configured SMTP relay via the stdlib only
  (no vendor SDK). Failures raise `EmailDeliveryError` with no secrets.

`send()` is kept as a backwards-compatible metadata-only shim.
"""

from __future__ import annotations

import logging
import smtplib
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage as StdEmailMessage
from typing import Protocol

from app.core.config import Settings, get_settings

log = logging.getLogger(__name__)

RESET_SUBJECT = "DigiTwin password reset"
SMTP_TIMEOUT_SECONDS = 10


class EmailDeliveryError(Exception):
    """SMTP transport failed. Never carries credentials, tokens, or URLs."""


@dataclass(frozen=True)
class EmailMessage:
    to: str
    sender: str
    subject: str
    text_body: str


class EmailSender(Protocol):
    def __call__(self, message: EmailMessage) -> None: ...


def build_reset_link(base_url: str, raw_token: str) -> str:
    """Public web link the user opens to set a new password."""
    return f"{base_url.rstrip('/')}/reset?token={raw_token}"


def build_password_reset_message(to: str, reset_link: str, *, sender: str) -> EmailMessage:
    body = (
        "You requested a DigiTwin password reset.\n\n"
        f"Use this link within one hour to choose a new password:\n{reset_link}\n\n"
        "The link is single-use. If you did not request this, you can safely "
        "ignore this email — your password is unchanged."
    )
    return EmailMessage(to=to, sender=sender, subject=RESET_SUBJECT, text_body=body)


def _to_std_message(message: EmailMessage) -> StdEmailMessage:
    std = StdEmailMessage()
    std["From"] = message.sender
    std["To"] = message.to
    std["Subject"] = message.subject
    std.set_content(message.text_body)
    return std


def _smtp_send(
    message: EmailMessage,
    settings: Settings,
    smtp_client: Callable[..., object] | None = None,
) -> None:
    """Deliver one message via SMTP. Raises EmailDeliveryError on any failure."""
    client_factory = smtp_client or smtplib.SMTP
    std = _to_std_message(message)
    try:
        smtp = client_factory(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS)
        try:
            if settings.smtp_use_tls:
                smtp.starttls()  # type: ignore[attr-defined]
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)  # type: ignore[attr-defined]
            smtp.send_message(std)  # type: ignore[attr-defined]
        finally:
            try:
                smtp.quit()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001, S110 — quit best-effort after send
                pass
    except EmailDeliveryError:
        raise
    except Exception as exc:  # normalize to a secret-free error
        raise EmailDeliveryError(f"SMTP delivery to {message.to} failed: {exc}") from exc
    log.info("password reset email sent to=%s via smtp host=%s", message.to, settings.smtp_host)


def _log_send(message: EmailMessage) -> None:
    # Metadata only: the token/link must never appear in normal application logs.
    log.info(
        "password reset email queued to=%s subject=%s (email_backend=log; "
        "configure SMTP for real delivery)",
        message.to,
        message.subject,
    )


def send_password_reset(
    to: str,
    raw_token: str,
    *,
    settings: Settings | None = None,
    sender: EmailSender | None = None,
    smtp_client: Callable[..., object] | None = None,
) -> None:
    """Build the reset link and deliver it through the configured backend.

    `sender` overrides dispatch (test seam); otherwise "smtp" delivers when
    `settings.smtp_configured`, else the metadata-only log backend is used.
    Raises EmailDeliveryError when the SMTP transport fails.
    """
    s = settings or get_settings()
    message = build_password_reset_message(
        to, build_reset_link(s.app_base_url, raw_token), sender=s.smtp_from
    )
    if sender is not None:
        sender(message)
        return
    if s.smtp_configured:
        _smtp_send(message, s, smtp_client=smtp_client)
    else:
        _log_send(message)


def send(to: str, subject: str, body: str, *, sensitive: bool = False) -> None:
    """Legacy shim: records metadata only, never the body (may hold secrets)."""
    del body, sensitive  # body is intentionally never logged
    log.info("email(to=%s, subject=%s)", to, subject)
