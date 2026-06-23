"""Tests for the transcription client (Phase 2.10b) — Deepgram pre-recorded by URL.

The external boundary (Deepgram API) is MOCKED. The request assembly (endpoint, Token
auth, model param, URL body) and the non-fatal failure handling are REAL. Deepgram
downloads + transcribes the audio server-side, so there is no local download/ffmpeg.
"""

from unittest.mock import Mock, patch

from src.transcription import transcribe_audio


def _dg_response(transcript="markets were choppy this week"):
    r = Mock()
    r.raise_for_status = Mock()
    r.json = Mock(return_value={"results": {"channels": [{"alternatives": [{"transcript": transcript}]}]}})
    return r


class TestTranscribeAudio:
    @patch("src.transcription.httpx.post")
    def test_happy_path_returns_text(self, mock_post):
        mock_post.return_value = _dg_response()
        assert transcribe_audio("https://cdn.x/ep.mp3", api_key="dg") == "markets were choppy this week"

    @patch("src.transcription.httpx.post")
    def test_sends_correct_request(self, mock_post):
        mock_post.return_value = _dg_response()
        transcribe_audio("https://cdn.x/ep.mp3", api_key="dg", model="nova-3")
        kwargs = mock_post.call_args[1]
        assert mock_post.call_args[0][0] == "https://api.deepgram.com/v1/listen"
        assert kwargs["headers"]["Authorization"] == "Token dg"
        assert kwargs["params"]["model"] == "nova-3"
        assert kwargs["json"] == {"url": "https://cdn.x/ep.mp3"}  # URL, not uploaded bytes

    def test_no_api_key_returns_none_without_network(self, monkeypatch):
        monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
        monkeypatch.setattr("src.transcription.httpx.post", Mock(side_effect=AssertionError("network!")))
        assert transcribe_audio("https://cdn.x/ep.mp3") is None

    @patch("src.transcription.httpx.post", side_effect=Exception("api down"))
    def test_api_error_returns_none(self, _mock_post):
        assert transcribe_audio("https://cdn.x/ep.mp3", api_key="dg") is None

    @patch("src.transcription.httpx.post")
    def test_empty_transcript_returns_none(self, mock_post):
        mock_post.return_value = _dg_response(transcript="   ")
        assert transcribe_audio("https://cdn.x/ep.mp3", api_key="dg") is None

    @patch("src.transcription.httpx.post")
    def test_env_model_override(self, mock_post, monkeypatch):
        monkeypatch.setenv("DEEPGRAM_MODEL", "nova-2")
        mock_post.return_value = _dg_response()
        transcribe_audio("https://cdn.x/ep.mp3", api_key="dg")
        assert mock_post.call_args[1]["params"]["model"] == "nova-2"
