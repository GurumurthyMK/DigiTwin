"""Outbound email seam. Phase 5B: logged delivery (visible in dev/console),
with the exact SMTP integration point documented. No email vendor SDK is added
until an ops owner configures one — the service layer depends only on `send`.

To go live: implement `_smtp_send` with SMTP_ env vars and flip
`settings.email_backend` to "smtp". Never log tokens in production.
"""

import logging

log = logging.getLogger(__name__)


def send(to: str, subject: str, body: str, *, sensitive: bool = False) -> None:
    """Deliver or, without an SMTP backend, log for operator pickup."""
    # NOTE: reset links contain secrets; the dev log is the delivery channel
    # until SMTP is configured. Production MUST set email_backend=smtp.
    if sensitive:
        log.warning("email(to=%s, subject=%s) body=%s", to, subject, body)
    else:
        log.info("email(to=%s, subject=%s)", to, subject)
