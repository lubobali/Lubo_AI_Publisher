"""Tests for token_manager.py — token expiry checking and email notifications."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from src.token_manager import build_reauth_url, check_token_expiry, days_until_expiry, send_expiry_email


class TestDaysUntilExpiry:
    def test_token_expires_in_30_days(self):
        """Token 30 days + 12 hours from now returns 30 full days."""
        expires_at = datetime.now(UTC) + timedelta(days=30, hours=12)
        assert days_until_expiry(expires_at) == 30

    def test_token_already_expired(self):
        """Expired token returns negative number."""
        expires_at = datetime.now(UTC) - timedelta(days=5)
        assert days_until_expiry(expires_at) < 0

    def test_token_expires_today(self):
        """Token expiring in a few hours returns 0."""
        expires_at = datetime.now(UTC) + timedelta(hours=3)
        assert days_until_expiry(expires_at) == 0


class TestCheckTokenExpiry:
    def test_warns_when_expiring_within_threshold(self):
        """5 days left, threshold is 7 — should warn."""
        expires_at = datetime.now(UTC) + timedelta(days=5, hours=12)
        assert check_token_expiry(expires_at, warn_days=7) is True

    def test_no_warn_when_plenty_of_time(self):
        """30 days left, threshold is 7 — no warning."""
        expires_at = datetime.now(UTC) + timedelta(days=30)
        assert check_token_expiry(expires_at, warn_days=7) is False

    def test_warns_when_already_expired(self):
        """Expired token — always warn."""
        expires_at = datetime.now(UTC) - timedelta(days=1)
        assert check_token_expiry(expires_at, warn_days=7) is True

    def test_warns_at_exactly_threshold(self):
        """Exactly 7 days left — should warn."""
        expires_at = datetime.now(UTC) + timedelta(days=7, hours=12)
        assert check_token_expiry(expires_at, warn_days=7) is True

    def test_no_warn_at_one_day_past_threshold(self):
        """8 days left — no warning."""
        expires_at = datetime.now(UTC) + timedelta(days=8, hours=12)
        assert check_token_expiry(expires_at, warn_days=7) is False


class TestBuildReauthUrl:
    def test_is_linkedin_oauth_url(self):
        """URL must point to LinkedIn's authorization endpoint."""
        url = build_reauth_url(client_id="test123", redirect_uri="http://localhost:8000/callback")
        assert url.startswith("https://www.linkedin.com/oauth/v2/authorization")

    def test_contains_client_id(self):
        """Re-auth URL must include the client ID."""
        url = build_reauth_url(client_id="test123", redirect_uri="http://localhost:8000/callback")
        assert "test123" in url

    def test_contains_required_oauth_params(self):
        """Must have response_type=code and w_member_social scope."""
        url = build_reauth_url(client_id="test123", redirect_uri="http://localhost:8000/callback")
        assert "response_type=code" in url
        assert "w_member_social" in url

    def test_contains_redirect_uri(self):
        """Must include the redirect URI."""
        url = build_reauth_url(client_id="test123", redirect_uri="http://localhost:8000/callback")
        assert "localhost" in url


class TestSendExpiryEmail:
    @patch("src.token_manager.smtplib.SMTP")
    def test_sends_email_successfully(self, mock_smtp_class):
        """Email sends without error and returns True."""
        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        result = send_expiry_email(
            smtp_host="mail.test.com",
            smtp_port=587,
            smtp_user="test@test.com",
            smtp_password="password",
            to_email="user@test.com",
            reauth_url="https://linkedin.com/oauth/test",
            days_left=5,
        )

        assert result is True
        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once_with("test@test.com", "password")
        mock_smtp.send_message.assert_called_once()

    @patch("src.token_manager.smtplib.SMTP")
    def test_email_body_contains_days_and_url(self, mock_smtp_class):
        """Email body should mention days left and include re-auth link."""
        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        reauth = "https://linkedin.com/oauth/test-url"
        send_expiry_email(
            smtp_host="mail.test.com",
            smtp_port=587,
            smtp_user="test@test.com",
            smtp_password="password",
            to_email="user@test.com",
            reauth_url=reauth,
            days_left=3,
        )

        sent_msg = mock_smtp.send_message.call_args[0][0]
        body = sent_msg.get_payload()
        assert "3" in body
        assert reauth in body

    @patch("src.token_manager.smtplib.SMTP")
    def test_returns_false_on_smtp_error(self, mock_smtp_class):
        """Should return False if SMTP fails, not crash."""
        mock_smtp_class.side_effect = ConnectionRefusedError("SMTP down")

        result = send_expiry_email(
            smtp_host="mail.test.com",
            smtp_port=587,
            smtp_user="test@test.com",
            smtp_password="password",
            to_email="user@test.com",
            reauth_url="https://linkedin.com/oauth/test",
            days_left=5,
        )

        assert result is False
