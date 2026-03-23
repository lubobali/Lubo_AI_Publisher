"""Tests for linkedin_client.py — OAuth flow and authenticated API calls."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.linkedin_client import (
    create_image_post,
    create_text_post,
    exchange_code_for_token,
    get_auth_headers,
    initialize_image_upload,
    upload_image,
)


class TestExchangeCodeForToken:
    @pytest.mark.asyncio
    @patch("src.linkedin_client.httpx.AsyncClient.post")
    async def test_returns_access_token_on_success(self, mock_post):
        """Successful code exchange returns access token and expiry."""
        mock_post.return_value = AsyncMock(
            status_code=200,
            json=lambda: {
                "access_token": "AQVtesttoken123",
                "expires_in": 5184000,
            },
        )

        result = await exchange_code_for_token(
            code="test_auth_code",
            client_id="test_client_id",
            client_secret="test_secret",
            redirect_uri="http://localhost:8000/callback",
        )

        assert result["access_token"] == "AQVtesttoken123"
        assert result["expires_in"] == 5184000

    @pytest.mark.asyncio
    @patch("src.linkedin_client.httpx.AsyncClient.post")
    async def test_sends_correct_grant_type(self, mock_post):
        """Must send grant_type=authorization_code."""
        mock_post.return_value = AsyncMock(
            status_code=200,
            json=lambda: {"access_token": "token", "expires_in": 5184000},
        )

        await exchange_code_for_token(
            code="code123",
            client_id="cid",
            client_secret="secret",
            redirect_uri="http://localhost:8000/callback",
        )

        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs["data"]["grant_type"] == "authorization_code"
        assert call_kwargs.kwargs["data"]["code"] == "code123"

    @pytest.mark.asyncio
    @patch("src.linkedin_client.httpx.AsyncClient.post")
    async def test_raises_on_linkedin_error(self, mock_post):
        """Should raise on non-200 response from LinkedIn."""
        mock_response = MagicMock(status_code=400)
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Bad Request", request=MagicMock(), response=mock_response
        )
        mock_post.return_value = mock_response

        with pytest.raises(httpx.HTTPStatusError):
            await exchange_code_for_token(
                code="bad_code",
                client_id="cid",
                client_secret="secret",
                redirect_uri="http://localhost:8000/callback",
            )


class TestGetAuthHeaders:
    def test_returns_bearer_token(self):
        """Headers must include Bearer token."""
        headers = get_auth_headers("AQVtesttoken123")
        assert headers["Authorization"] == "Bearer AQVtesttoken123"

    def test_includes_linkedin_version(self):
        """LinkedIn API requires version header."""
        headers = get_auth_headers("AQVtesttoken123")
        assert "LinkedIn-Version" in headers

    def test_includes_restli_header(self):
        """LinkedIn REST API uses restli protocol version 2."""
        headers = get_auth_headers("AQVtesttoken123")
        assert headers.get("X-Restli-Protocol-Version") == "2.0.0"


class TestCreateTextPost:
    @pytest.mark.asyncio
    @patch("src.linkedin_client.httpx.AsyncClient.post")
    async def test_returns_post_urn_on_success(self, mock_post):
        """Successful post returns the LinkedIn post URN."""
        mock_response = MagicMock(status_code=201)
        mock_response.headers = {"x-restli-id": "urn:li:share:123456"}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        result = await create_text_post(
            access_token="AQVtoken",
            person_urn="urn:li:person:abc123",
            text="Just tested the new model. The results were wild.",
        )

        assert result == "urn:li:share:123456"

    @pytest.mark.asyncio
    @patch("src.linkedin_client.httpx.AsyncClient.post")
    async def test_sends_correct_post_body(self, mock_post):
        """Request body must match LinkedIn's /rest/posts schema."""
        mock_response = MagicMock(status_code=201)
        mock_response.headers = {"x-restli-id": "urn:li:share:789"}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        await create_text_post(
            access_token="AQVtoken",
            person_urn="urn:li:person:abc123",
            text="Test post content",
        )

        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs["json"]
        assert body["author"] == "urn:li:person:abc123"
        assert body["commentary"] == "Test post content"
        assert body["visibility"] == "PUBLIC"
        assert body["distribution"]["feedDistribution"] == "MAIN_FEED"
        assert body["lifecycleState"] == "PUBLISHED"

    @pytest.mark.asyncio
    @patch("src.linkedin_client.httpx.AsyncClient.post")
    async def test_posts_to_correct_endpoint(self, mock_post):
        """Must POST to /rest/posts."""
        mock_response = MagicMock(status_code=201)
        mock_response.headers = {"x-restli-id": "urn:li:share:999"}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        await create_text_post(
            access_token="AQVtoken",
            person_urn="urn:li:person:abc123",
            text="Test",
        )

        call_args = mock_post.call_args
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
        assert "/rest/posts" in str(url)


class TestInitializeImageUpload:
    @pytest.mark.asyncio
    @patch("src.linkedin_client.httpx.AsyncClient.post")
    async def test_returns_upload_url_and_image_urn(self, mock_post):
        """Successful init returns upload URL and image URN."""
        mock_response = MagicMock(status_code=200)
        mock_response.json = lambda: {
            "value": {
                "uploadUrl": "https://api.linkedin.com/mediaUpload/image/xyz",
                "image": "urn:li:image:abc123",
            }
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        upload_url, image_urn = await initialize_image_upload(
            access_token="AQVtoken",
            person_urn="urn:li:person:abc123",
        )

        assert upload_url == "https://api.linkedin.com/mediaUpload/image/xyz"
        assert image_urn == "urn:li:image:abc123"

    @pytest.mark.asyncio
    @patch("src.linkedin_client.httpx.AsyncClient.post")
    async def test_sends_owner_in_request_body(self, mock_post):
        """Must include owner (person URN) in init request."""
        mock_response = MagicMock(status_code=200)
        mock_response.json = lambda: {
            "value": {
                "uploadUrl": "https://example.com/upload",
                "image": "urn:li:image:999",
            }
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        await initialize_image_upload(
            access_token="AQVtoken",
            person_urn="urn:li:person:abc123",
        )

        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs["json"]
        assert body["initializeUploadRequest"]["owner"] == "urn:li:person:abc123"


class TestUploadImage:
    @pytest.mark.asyncio
    @patch("src.linkedin_client.httpx.AsyncClient.put")
    async def test_uploads_binary_to_url(self, mock_put):
        """Must PUT image bytes to the upload URL."""
        mock_response = MagicMock(status_code=201)
        mock_response.raise_for_status = MagicMock()
        mock_put.return_value = mock_response

        image_bytes = b"fake-png-data"
        await upload_image(
            access_token="AQVtoken",
            upload_url="https://api.linkedin.com/mediaUpload/image/xyz",
            image_data=image_bytes,
        )

        call_kwargs = mock_put.call_args
        url = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("url", "")
        assert "mediaUpload" in str(url)
        assert call_kwargs.kwargs["content"] == image_bytes


class TestCreateImagePost:
    @pytest.mark.asyncio
    @patch("src.linkedin_client.httpx.AsyncClient.post")
    async def test_returns_post_urn(self, mock_post):
        """Successful image post returns the post URN."""
        mock_response = MagicMock(status_code=201)
        mock_response.headers = {"x-restli-id": "urn:li:share:img456"}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        result = await create_image_post(
            access_token="AQVtoken",
            person_urn="urn:li:person:abc123",
            text="Check out this screenshot",
            image_urn="urn:li:image:xyz789",
        )

        assert result == "urn:li:share:img456"

    @pytest.mark.asyncio
    @patch("src.linkedin_client.httpx.AsyncClient.post")
    async def test_body_includes_image_content_block(self, mock_post):
        """Request body must include content.media with image URN."""
        mock_response = MagicMock(status_code=201)
        mock_response.headers = {"x-restli-id": "urn:li:share:img789"}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        await create_image_post(
            access_token="AQVtoken",
            person_urn="urn:li:person:abc123",
            text="Screenshot post",
            image_urn="urn:li:image:xyz789",
        )

        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs["json"]
        assert body["author"] == "urn:li:person:abc123"
        assert body["commentary"] == "Screenshot post"
        assert body["content"]["media"]["id"] == "urn:li:image:xyz789"
        assert body["visibility"] == "PUBLIC"
