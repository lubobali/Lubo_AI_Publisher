"""LinkedIn API client — OAuth flow and authenticated API calls."""

import httpx

LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"


async def exchange_code_for_token(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    """Exchange an OAuth authorization code for an access token."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            LINKEDIN_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        return response.json()


LINKEDIN_API_VERSION = "202401"


def get_auth_headers(access_token: str) -> dict:
    """Return headers required for authenticated LinkedIn API calls."""
    return {
        "Authorization": f"Bearer {access_token}",
        "LinkedIn-Version": LINKEDIN_API_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


LINKEDIN_API_BASE = "https://api.linkedin.com"


async def create_text_post(
    access_token: str,
    person_urn: str,
    text: str,
) -> str:
    """Create a text-only post on LinkedIn. Returns the post URN."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url=f"{LINKEDIN_API_BASE}/rest/posts",
            headers=get_auth_headers(access_token),
            json={
                "author": person_urn,
                "commentary": text,
                "visibility": "PUBLIC",
                "distribution": {
                    "feedDistribution": "MAIN_FEED",
                    "targetEntities": [],
                    "thirdPartyDistributionChannels": [],
                },
                "lifecycleState": "PUBLISHED",
            },
        )
        response.raise_for_status()
        return response.headers.get("x-restli-id", "")


async def initialize_image_upload(
    access_token: str,
    person_urn: str,
) -> tuple[str, str]:
    """Initialize image upload on LinkedIn. Returns (upload_url, image_urn)."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url=f"{LINKEDIN_API_BASE}/rest/images?action=initializeUpload",
            headers=get_auth_headers(access_token),
            json={
                "initializeUploadRequest": {
                    "owner": person_urn,
                }
            },
        )
        response.raise_for_status()
        data = response.json()["value"]
        return data["uploadUrl"], data["image"]


async def upload_image(
    access_token: str,
    upload_url: str,
    image_data: bytes,
) -> None:
    """Upload image binary to LinkedIn's upload URL."""
    async with httpx.AsyncClient() as client:
        response = await client.put(
            url=upload_url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/octet-stream",
            },
            content=image_data,
        )
        response.raise_for_status()


async def create_image_post(
    access_token: str,
    person_urn: str,
    text: str,
    image_urn: str,
) -> str:
    """Create a post with an image on LinkedIn. Returns the post URN."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url=f"{LINKEDIN_API_BASE}/rest/posts",
            headers=get_auth_headers(access_token),
            json={
                "author": person_urn,
                "commentary": text,
                "visibility": "PUBLIC",
                "distribution": {
                    "feedDistribution": "MAIN_FEED",
                    "targetEntities": [],
                    "thirdPartyDistributionChannels": [],
                },
                "content": {
                    "media": {
                        "id": image_urn,
                    }
                },
                "lifecycleState": "PUBLISHED",
            },
        )
        response.raise_for_status()
        return response.headers.get("x-restli-id", "")
