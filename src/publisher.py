"""Platform-agnostic publisher interface — base class + implementations."""

import asyncio
import logging
from abc import ABC, abstractmethod

from src import x_client
from src.linkedin_client import (
    create_image_post,
    create_multi_image_post,
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

    async def publish_images(self, text: str, images: list[bytes]) -> str:
        """Publish a post with one or more images (a carousel). Default falls back to a
        single-image post (first image); platforms override for true multi-image."""
        return await self.publish_image(text, images[0])

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

    async def publish_images(self, text: str, images: list[bytes]) -> str:
        if len(images) <= 1:
            return await self.publish_image(text, images[0])
        # Upload each image -> collect URNs -> one multi-image (carousel) post.
        urns = []
        for image_data in images:
            upload_url, image_urn = await initialize_image_upload(
                access_token=self._access_token, person_urn=self._person_urn
            )
            await upload_image(access_token=self._access_token, upload_url=upload_url, image_data=image_data)
            urns.append(image_urn)
        return await create_multi_image_post(
            access_token=self._access_token, person_urn=self._person_urn, text=text, image_urns=urns
        )

    def get_post_url(self, post_urn: str) -> str:
        # LinkedIn post URNs look like: urn:li:share:7123456789
        share_id = post_urn.split(":")[-1]
        return f"https://www.linkedin.com/feed/update/urn:li:share:{share_id}"


class XPublisher(Publisher):
    """X (Twitter) publisher — wraps x_client (tweepy OAuth 1.0a). Reads X_* env vars.

    tweepy is synchronous, so each call runs in a thread to keep the event loop free.
    Adds reply() for the self-reply link (X-specific — links go in a reply, not the post).
    """

    @property
    def platform_name(self) -> str:
        return "x"

    async def publish_text(self, text: str) -> str:
        return await asyncio.to_thread(x_client.post_text, text)

    async def publish_image(self, text: str, image_data: bytes) -> str:
        return await asyncio.to_thread(x_client.post_image, text, image_data)

    async def publish_images(self, text: str, images: list[bytes]) -> str:
        if len(images) <= 1:
            return await self.publish_image(text, images[0])
        return await asyncio.to_thread(x_client.post_images, text, images)

    async def reply(self, in_reply_to_id: str, text: str) -> str:
        """Post a reply to one of our tweets (the self-reply link). Returns reply id."""
        return await asyncio.to_thread(x_client.reply, in_reply_to_id, text)

    def get_post_url(self, post_urn: str) -> str:
        return f"https://x.com/i/web/status/{post_urn}"


# ---------------------------------------------------------------------------
# Platform registry
# ---------------------------------------------------------------------------

_PLATFORM_MAP = {
    "linkedin": LinkedInPublisher,
    "x": XPublisher,
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
