"""Tests for post-processing pipeline — enforces rules the LLM can't be trusted to follow."""

from src.post_processor import (
    deduplicate_hashtags,
    enforce_line_breaks,
    ensure_closing_question_mark,
    ensure_paragraph_spacing,
    limit_hashtags,
    normalize_brand,
    process_post,
    strip_apostrophes,
    strip_dashes,
    strip_filler_phrases,
    strip_json_wrapper,
    strip_markdown,
    strip_model_meta,
    strip_special_chars,
    validate_post,
)


class TestEnsureClosingQuestionMark:
    """Safety net: reasoning models sometimes drop the '?' on the closing question."""

    def test_adds_question_mark_when_missing(self):
        t = "markets ripped this week\n\nwhat would your portfolio look like if you stopped fighting"
        assert ensure_closing_question_mark(t).rstrip().endswith("?")

    def test_leaves_existing_question_mark(self):
        t = "some thoughts\n\nare you buying this dip?"
        assert ensure_closing_question_mark(t) == t

    def test_does_not_touch_non_question_close(self):
        t = "just shipped a thing\n\nwild times"
        assert ensure_closing_question_mark(t) == t  # not a question -> left alone

    def test_process_post_repairs_missing_question_mark(self):
        body = (
            "emerging markets up almost five percent while the broad index barely moved. "
            "that divergence tells you where the money is actually flowing this week. "
            "the tech names keep carrying the load while everything else just sits there. "
            "the risk is chasing the hot hand because sentiment turns fast in these spots. "
            "still, real diversification means owning things that do not move together.\n\n"
            "what would your portfolio look like if you stopped fighting the rotation"
        )
        text, _ = process_post(body, ["#x"])
        assert text.rstrip().endswith("?")  # dropped '?' repaired
        ok, _reason = validate_post(text)
        assert ok is True  # now passes the engagement-question rule


class TestNormalizeBrand:
    def test_fixes_brand_casing(self):
        assert normalize_brand("luBot is great and lubot rocks, Lubot wins") == (
            "LuBot is great and LuBot rocks, LuBot wins"
        )

    def test_leaves_correct_casing(self):
        assert normalize_brand("LuBot shipped today") == "LuBot shipped today"

    def test_fixes_lobot_typo(self):
        assert normalize_brand("lobot ate 61 hours") == "LuBot ate 61 hours"

    def test_does_not_touch_domain(self):
        assert normalize_brand("visit lubot.ai and staging.lubot.ai") == "visit lubot.ai and staging.lubot.ai"


class TestStripMarkdown:
    def test_removes_bold(self):
        assert strip_markdown("this is **bold** text") == "this is bold text"

    def test_removes_inline_italic_and_code(self):
        assert "*" not in strip_markdown("some *italic* and `code` here")
        assert "`" not in strip_markdown("some *italic* and `code` here")

    def test_strips_header_markers_keeps_text(self):
        assert strip_markdown("## The Fix") == "The Fix"
        assert strip_markdown("### Why it matters") == "Why it matters"

    def test_drops_setext_underline_lines(self):
        out = strip_markdown("My Title\n=====\nbody")
        assert "=====" not in out
        assert "My Title" in out and "body" in out

    def test_converts_star_bullets_to_dash(self):
        assert strip_markdown("* first\n* second").splitlines()[0].startswith("- ")

    def test_plain_text_untouched(self):
        assert strip_markdown("just a normal sentence here") == "just a normal sentence here"


class TestStripModelMeta:
    def test_removes_rule_leak_brackets(self):
        out = strip_model_meta("queries took [NO NUMBER - AS PER RULES, SINCE NONE GIVEN] too long")
        assert "NO NUMBER" not in out
        assert "queries took" in out and "too long" in out

    def test_drops_label_lines(self):
        text = 'LinkedIn Post\n\nReal content here.\n\nScreenshot URL: null\nHashtags: ["#AI"]'
        out = strip_model_meta(text)
        assert "Real content here." in out
        assert "LinkedIn Post" not in out
        assert "Screenshot URL" not in out
        assert "Hashtags" not in out

    def test_keeps_normal_content(self):
        text = "I shipped a feature today.\n\nIt was hard but worth it."
        assert strip_model_meta(text) == text


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
# strip_special_chars — plain human typing (no arrows/fancy unicode)
# ---------------------------------------------------------------------------


class TestStripSpecialChars:
    def test_replaces_ascii_arrow(self):
        assert "->" not in strip_special_chars("free way -> premium way")

    def test_replaces_unicode_arrow(self):
        out = strip_special_chars("free way → premium way")
        assert "→" not in out

    def test_replaces_ellipsis_and_bullet(self):
        out = strip_special_chars("wait… • do this")
        assert "…" not in out and "•" not in out

    def test_strips_smart_quotes(self):
        out = strip_special_chars("he said “hi” to me")
        assert "“" not in out and "”" not in out

    def test_leaves_plain_text_alone(self):
        assert strip_special_chars("just normal text here") == "just normal text here"

    def test_process_post_strips_arrows(self):
        text, _ = process_post("Stop seed oils -> feel better. What did you change?", [])
        assert "->" not in text and "→" not in text


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


class TestNumbersGrounded:
    """Zero-BS guardrail: post numbers must trace to the source market data."""

    SOURCE = "S&P 500: closed 7,503.45, +1.0% on the week. Nasdaq: closed 26,520.82, +2.4%."

    def test_grounded_when_all_numbers_from_source(self):
        from src.post_processor import numbers_grounded

        post = "the index closed 7,503.45 this week, up 1.0 percent. nasdaq 26,520.82 up 2.4"
        ok, bad = numbers_grounded(post, self.SOURCE)
        assert ok is True
        assert bad == set()

    def test_flags_fabricated_number(self):
        from src.post_processor import numbers_grounded

        post = "the market hit 9,999.99 and i made 45000 dollars this week"
        ok, bad = numbers_grounded(post, self.SOURCE)
        assert ok is False
        assert "9999.99" in bad
        assert "45000" in bad

    def test_skips_trivial_small_integers(self):
        from src.post_processor import numbers_grounded

        post = "3 indices, 1 thing i learned, 2 takeaways"  # no real data claims
        ok, bad = numbers_grounded(post, self.SOURCE)
        assert ok is True

    def test_tolerates_rounding(self):
        from src.post_processor import numbers_grounded

        post = "around 7500 on the week"  # rounded from 7,503.45
        ok, _ = numbers_grounded(post, self.SOURCE)
        assert ok is True
