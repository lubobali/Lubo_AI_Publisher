"""X (Twitter) API client — thin tweepy wrapper for posting Lubo's own content.

External boundary = the tweepy network calls. Kept as small sync functions so the publisher
layer (XPublisher) wraps them and tests mock at this boundary. OAuth 1.0a user context
(post text/image + self-reply + delete). Credentials come from the X_* env vars. Image upload
uses the v1.1 media endpoint (confirmed working on pay-per-use); posting/reply use the v2 API.
"""

import io
import logging
import os

logger = logging.getLogger(__name__)

_REQUIRED = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")


def x_configured() -> bool:
    """True only when all X OAuth 1.0a credentials are present (else X is skipped)."""
    return all(os.getenv(k) for k in _REQUIRED)


def _clients():
    """Build (v1.1 API for media upload, v2 Client for posting). tweepy imported lazily."""
    import tweepy

    auth = tweepy.OAuth1UserHandler(
        os.environ["X_API_KEY"],
        os.environ["X_API_SECRET"],
        os.environ["X_ACCESS_TOKEN"],
        os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    api = tweepy.API(auth)
    client = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    return api, client


def post_text(text: str) -> str:
    """Post a text tweet. Returns the tweet id."""
    _, client = _clients()
    return str(client.create_tweet(text=text).data["id"])


def post_image(text: str, image_data: bytes, filename: str = "card.png") -> str:
    """Upload an image (v1.1) then post a tweet with it (v2). Returns the tweet id."""
    api, client = _clients()
    media = api.media_upload(filename=filename, file=io.BytesIO(image_data))
    return str(client.create_tweet(text=text, media_ids=[media.media_id]).data["id"])


def post_images(text: str, images: list[bytes]) -> str:
    """Upload up to 4 images (v1.1) then post one tweet with all of them (v2). Returns the id."""
    api, client = _clients()
    media_ids = [
        api.media_upload(filename=f"img{i}.png", file=io.BytesIO(data)).media_id for i, data in enumerate(images[:4])
    ]
    return str(client.create_tweet(text=text, media_ids=media_ids).data["id"])


def reply(in_reply_to_id: str, text: str) -> str:
    """Reply to a tweet (used for the self-reply link). Returns the reply tweet id."""
    _, client = _clients()
    return str(client.create_tweet(text=text, in_reply_to_tweet_id=in_reply_to_id).data["id"])


def delete(tweet_id: str) -> None:
    """Delete a tweet (test cleanup / retraction)."""
    _, client = _clients()
    client.delete_tweet(tweet_id)
