"""Token expiry management for LinkedIn OAuth tokens."""

import logging
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


def days_until_expiry(expires_at: datetime) -> int:
    """Return number of whole days until token expires."""
    delta = expires_at - datetime.now(UTC)
    return delta.days


def check_token_expiry(expires_at: datetime, warn_days: int = 7) -> bool:
    """Return True if token is expiring within warn_days (or already expired)."""
    return days_until_expiry(expires_at) <= warn_days


LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"


def build_reauth_url(client_id: str, redirect_uri: str) -> str:
    """Build the LinkedIn OAuth authorization URL for re-authentication."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "w_member_social",
    }
    return f"{LINKEDIN_AUTH_URL}?{urlencode(params)}"


def send_expiry_email(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    to_email: str,
    reauth_url: str,
    days_left: int,
) -> bool:
    """Send token expiry warning email with re-auth link. Returns True on success."""
    msg = EmailMessage()
    msg["Subject"] = f"LuBot Publisher: LinkedIn token expires in {days_left} days"
    msg["From"] = smtp_user
    msg["To"] = to_email
    msg.set_content(
        f"Your LinkedIn access token expires in {days_left} days.\n\n"
        f"Click this link to re-authorize (takes 30 seconds):\n\n"
        f"{reauth_url}\n\n"
        f"After clicking, the new token will be saved automatically."
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        logger.info("Expiry warning email sent to %s", to_email)
        return True
    except Exception:
        logger.exception("Failed to send expiry email")
        return False
