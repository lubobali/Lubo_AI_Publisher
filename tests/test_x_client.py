"""Tests for the X (Twitter) client — tweepy wrapper.

External boundary (tweepy v1.1 API + v2 Client) is MOCKED via _clients(); the request
assembly (text, media_ids, in_reply_to) and id parsing are REAL.
"""

from unittest.mock import MagicMock, patch

from src import x_client


class TestXConfigured:
    KEYS = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")

    def test_true_when_all_set(self, monkeypatch):
        for k in self.KEYS:
            monkeypatch.setenv(k, "v")
        assert x_client.x_configured() is True

    def test_false_when_any_missing(self, monkeypatch):
        for k in self.KEYS:
            monkeypatch.setenv(k, "v")
        monkeypatch.delenv("X_ACCESS_TOKEN", raising=False)
        assert x_client.x_configured() is False


def _fake_clients():
    api = MagicMock()
    client = MagicMock()
    client.create_tweet.return_value = MagicMock(data={"id": 12345})
    api.media_upload.return_value = MagicMock(media_id=999)
    return api, client


class TestPosting:
    def test_post_text_returns_id(self):
        api, client = _fake_clients()
        with patch.object(x_client, "_clients", return_value=(api, client)):
            assert x_client.post_text("hello") == "12345"
            client.create_tweet.assert_called_once_with(text="hello")

    def test_post_image_uploads_then_attaches(self):
        api, client = _fake_clients()
        with patch.object(x_client, "_clients", return_value=(api, client)):
            assert x_client.post_image("hi", b"PNGBYTES") == "12345"
            api.media_upload.assert_called_once()
            assert client.create_tweet.call_args.kwargs["media_ids"] == [999]

    def test_reply_sets_in_reply_to(self):
        api, client = _fake_clients()
        with patch.object(x_client, "_clients", return_value=(api, client)):
            x_client.reply("777", "lubot.ai")
            kw = client.create_tweet.call_args.kwargs
            assert kw["in_reply_to_tweet_id"] == "777"
            assert kw["text"] == "lubot.ai"

    def test_delete(self):
        api, client = _fake_clients()
        with patch.object(x_client, "_clients", return_value=(api, client)):
            x_client.delete("55")
            client.delete_tweet.assert_called_once_with("55")
