"""Platform-agnostic publisher interface — base class + implementations."""

import logging
from abc import ABC, abstractmethod

from src.linkedin_client import (
    create_image_post,
    create_text_post,
    initialize_image_upload,
    upload_image,
)

logger = logging.getLogger(__name__)


class Publisher(ABC):
    """Base class for all platform publishers."""

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Return the platform identifier (e.g. 'linkedin', 'x')."""

    @abstractmethod
    async def publish_text(self, text: str) -> str:
        """Publish a text-only post. Returns platform-specific post ID/URN."""

    @abstractmethod
    async def publish_image(self, text: str, image_data: bytes) -> str:
        """Publish a post with image. Returns platform-specific post ID/URN."""

    @abstractmethod
    def get_post_url(self, post_urn: str) -> str:
        """Return the public URL for a published post."""


class LinkedInPublisher(Publisher):
    """LinkedIn publisher — wraps linkedin_client.py functions."""

    def __init__(self, access_token: str, person_urn: str):
        self._access_token = access_token
        self._person_urn = person_urn

    @property
    def platform_name(self) -> str:
        return "linkedin"

    async def publish_text(self, text: str) -> str:
        return await create_text_post(
            access_token=self._access_token,
            person_urn=self._person_urn,
            text=text,
        )

    async def publish_image(self, text: str, image_data: bytes) -> str:
        upload_url, image_urn = await initialize_image_upload(
            access_token=self._access_token,
            person_urn=self._person_urn,
        )

        await upload_image(
            access_token=self._access_token,
            upload_url=upload_url,
            image_data=image_data,
        )

        return await create_image_post(
            access_token=self._access_token,
            person_urn=self._person_urn,
            text=text,
            image_urn=image_urn,
        )

    def get_post_url(self, post_urn: str) -> str:
        # LinkedIn post URNs look like: urn:li:share:7123456789
        share_id = post_urn.split(":")[-1]
        return f"https://www.linkedin.com/feed/update/urn:li:share:{share_id}"


# ---------------------------------------------------------------------------
# Platform registry
# ---------------------------------------------------------------------------

_PLATFORM_MAP = {
    "linkedin": LinkedInPublisher,
}


def list_platforms() -> list[str]:
    """Return list of available platform names."""
    return list(_PLATFORM_MAP.keys())


def get_publisher(platform: str, **kwargs) -> Publisher | None:
    """Get a publisher instance for the given platform.

    Returns None if the platform is not supported.
    Pass platform-specific kwargs (e.g. access_token, person_urn).
    """
    cls = _PLATFORM_MAP.get(platform)
    if cls is None:
        logger.warning("Unknown platform: %s", platform)
        return None
    return cls(**kwargs)
