"""Tests for post-processing pipeline — enforces rules the LLM can't be trusted to follow."""

from src.post_processor import (
    deduplicate_hashtags,
    enforce_line_breaks,
    ensure_paragraph_spacing,
    limit_hashtags,
    process_post,
    strip_apostrophes,
    strip_dashes,
    strip_filler_phrases,
    strip_json_wrapper,
    validate_post,
)

# ---------------------------------------------------------------------------
# strip_dashes
# ---------------------------------------------------------------------------


class TestStripDashes:
    def test_replaces_em_dash(self):
        assert "AI - the future" in strip_dashes("AI — the future")

    def test_replaces_en_dash(self):
        assert "AI - the future" in strip_dashes("AI – the future")

    def test_leaves_hyphens_alone(self):
        assert strip_dashes("self-hosted") == "self-hosted"

    def test_handles_multiple_dashes(self):
        result = strip_dashes("AI — fast — cheap — good")
        assert "—" not in result
        assert "–" not in result


# ---------------------------------------------------------------------------
# strip_apostrophes
# ---------------------------------------------------------------------------


class TestStripApostrophes:
    def test_strips_common_contractions(self):
        text = "I'm building something. Don't stop. It's working. Can't believe it."
        result = strip_apostrophes(text)
        assert "'" not in result
        assert "im building" in result
        assert "Dont stop" in result
        assert "Its working" in result
        assert "Cant believe" in result

    def test_preserves_non_contraction_text(self):
        text = "AI is wild. Data moves fast."
        assert strip_apostrophes(text) == text

    def test_handles_lets(self):
        result = strip_apostrophes("Let's go")
        assert "'" not in result
        assert result == "Lets go"

    def test_handles_whats(self):
        result = strip_apostrophes("What's your take")
        assert "'" not in result
        assert result == "Whats your take"


# ---------------------------------------------------------------------------
# strip_json_wrapper
# ---------------------------------------------------------------------------


class TestStripJsonWrapper:
    def test_extracts_post_text_from_json(self):
        raw = '{"post_text": "Hello world.", "hashtags": ["#AI"]}'
        assert strip_json_wrapper(raw) == "Hello world."

    def test_leaves_plain_text_unchanged(self):
        text = "Just a normal post about AI."
        assert strip_json_wrapper(text) == text

    def test_handles_escaped_newlines(self):
        raw = '{"post_text": "Line 1.\\nLine 2.", "hashtags": ["#AI"]}'
        result = strip_json_wrapper(raw)
        assert "Line 1." in result
        assert "Line 2." in result

    def test_handles_raw_newlines_in_json(self):
        """253B puts raw newlines inside JSON string values."""
        raw = '{"post_text": "Line 1.\nLine 2.\nLine 3.", "hashtags": ["#AI"]}'
        result = strip_json_wrapper(raw)
        assert "Line 1." in result
        assert result != raw


# ---------------------------------------------------------------------------
# enforce_line_breaks
# ---------------------------------------------------------------------------


class TestEnforceLineBreaks:
    def test_breaks_long_paragraph(self):
        text = "First sentence. Second sentence. Third sentence. Fourth sentence. Fifth sentence."
        result = enforce_line_breaks(text)
        assert "\n" in result

    def test_leaves_short_paragraphs_alone(self):
        text = "Short sentence.\nAnother one."
        assert enforce_line_breaks(text) == text

    def test_preserves_existing_breaks(self):
        text = "Line 1.\n\nLine 2.\n\nLine 3."
        assert enforce_line_breaks(text) == text


# ---------------------------------------------------------------------------
# strip_filler_phrases
# ---------------------------------------------------------------------------


class TestEnsureParagraphSpacing:
    def test_inserts_blank_lines_in_wall_of_text(self):
        text = "Line 1.\nLine 2.\nLine 3.\nLine 4.\nLine 5.\nLine 6."
        result = ensure_paragraph_spacing(text)
        assert "\n\n" in result

    def test_preserves_existing_blank_lines(self):
        text = "Para 1 line 1.\nPara 1 line 2.\n\nPara 2 line 1.\nPara 2 line 2."
        result = ensure_paragraph_spacing(text)
        assert result == text

    def test_no_triple_blank_lines(self):
        text = "Line 1.\n\n\n\nLine 2."
        result = ensure_paragraph_spacing(text)
        assert "\n\n\n" not in result

    def test_short_text_unchanged(self):
        text = "Line 1.\nLine 2.\nLine 3."
        result = ensure_paragraph_spacing(text)
        assert result == text

    def test_dash_lists_stay_together(self):
        text = "Intro.\n\n- item 1\n- item 2\n- item 3\n\nClosing."
        result = ensure_paragraph_spacing(text)
        # Items should still be together (3 consecutive = OK)
        assert "- item 1\n- item 2\n- item 3" in result


class TestStripFillerPhrases:
    def test_removes_as_someone_who(self):
        text = "As someone who builds AI, this is cool."
        result = strip_filler_phrases(text)
        assert "As someone who" not in result

    def test_removes_let_me_tell_you(self):
        text = "Let me tell you why this matters."
        result = strip_filler_phrases(text)
        assert "Let me tell you" not in result

    def test_removes_heres_the_thing(self):
        text = "Here's the thing about AI agents."
        result = strip_filler_phrases(text)
        assert "Here's the thing" not in result

    def test_leaves_clean_text_alone(self):
        text = "AI agents are changing everything."
        assert strip_filler_phrases(text) == text


# ---------------------------------------------------------------------------
# hashtag utilities
# ---------------------------------------------------------------------------


class TestHashtagUtils:
    def test_deduplicate_preserves_order(self):
        tags = ["#AI", "#Tech", "#AI", "#Data", "#Tech"]
        assert deduplicate_hashtags(tags) == ["#AI", "#Tech", "#Data"]

    def test_limit_hashtags(self):
        tags = ["#a", "#b", "#c", "#d", "#e", "#f", "#g"]
        assert len(limit_hashtags(tags, 5)) == 5

    def test_limit_keeps_order(self):
        tags = ["#AI", "#Data", "#Tech", "#NVIDIA", "#Python", "#Extra"]
        result = limit_hashtags(tags, 5)
        assert result == ["#AI", "#Data", "#Tech", "#NVIDIA", "#Python"]


# ---------------------------------------------------------------------------
# validate_post
# ---------------------------------------------------------------------------


class TestValidatePost:
    def test_rejects_too_short(self):
        ok, reason = validate_post("Too short.")
        assert not ok
        assert "short" in reason.lower()

    def test_rejects_em_dashes(self):
        text = "A" * 400 + " — this has a dash. What do you think?"
        ok, reason = validate_post(text)
        assert not ok
        assert "dash" in reason.lower()

    def test_rejects_json_fragments(self):
        text = '{"post_text": "' + "A" * 400 + '"}'
        ok, reason = validate_post(text)
        assert not ok

    def test_accepts_good_post(self):
        text = (
            "Just saw this wild story about AI chips.\n"
            "What is going on here.\n\n"
            "The tech part fascinates me.\n"
            "Supermicro co-founder indicted for smuggling NVIDIA chips.\n"
            "Who else is watching this space?\n"
        ) * 3
        ok, _ = validate_post(text)
        assert ok

    def test_rejects_no_question(self):
        text = "A" * 450 + ". No question here. Just statements."
        ok, reason = validate_post(text)
        assert not ok
        assert "question" in reason.lower()


# ---------------------------------------------------------------------------
# process_post — full pipeline
# ---------------------------------------------------------------------------


class TestProcessPost:
    def test_full_pipeline(self):
        text = '{"post_text": "As someone who builds AI — this is wild. Let me tell you why.", "hashtags": ["#AI", "#AI", "#Tech"]}'
        hashtags = ["#AI", "#AI", "#Tech", "#Extra1", "#Extra2", "#Extra3"]
        result_text, result_tags = process_post(text, hashtags)
        assert "—" not in result_text
        assert "As someone who" not in result_text
        assert len(result_tags) <= 5
        assert result_tags.count("#AI") == 1
