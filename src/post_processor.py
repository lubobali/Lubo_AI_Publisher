"""Post-processing pipeline — enforces rules the LLM can't be trusted to follow.

Runs deterministic cleanup on generated posts before saving to DB.
"""

import json
import logging
import re

from src.observability import get_client, observe

logger = logging.getLogger(__name__)

# Number of fix categories tracked for compliance scoring
_MAX_FIX_CATEGORIES = 6


def strip_dashes(text: str) -> str:
    """Replace em dashes and en dashes with regular hyphens."""
    text = text.replace("\u2014", " - ")  # em dash
    text = text.replace("\u2013", " - ")  # en dash
    # Collapse double spaces from replacement
    text = re.sub(r"  +", " ", text)
    return text


# Apostrophe contractions -> ESL style (Lubo never uses apostrophes)
_APOSTROPHE_MAP = {
    "I'm": "im",
    "i'm": "im",
    "I've": "ive",
    "i've": "ive",
    "I'll": "ill",
    "i'll": "ill",
    "I'd": "id",
    "i'd": "id",
    "don't": "dont",
    "Don't": "Dont",
    "doesn't": "doesnt",
    "Doesn't": "Doesnt",
    "didn't": "didnt",
    "Didn't": "Didnt",
    "can't": "cant",
    "Can't": "Cant",
    "won't": "wont",
    "Won't": "Wont",
    "isn't": "isnt",
    "Isn't": "Isnt",
    "aren't": "arent",
    "Aren't": "Arent",
    "wasn't": "wasnt",
    "Wasn't": "Wasnt",
    "wouldn't": "wouldnt",
    "Wouldn't": "Wouldnt",
    "couldn't": "couldnt",
    "Couldn't": "Couldnt",
    "shouldn't": "shouldnt",
    "Shouldn't": "Shouldnt",
    "haven't": "havent",
    "Haven't": "Havent",
    "hasn't": "hasnt",
    "Hasn't": "Hasnt",
    "it's": "its",
    "It's": "Its",
    "that's": "thats",
    "That's": "Thats",
    "what's": "whats",
    "What's": "Whats",
    "there's": "theres",
    "There's": "Theres",
    "here's": "heres",
    "Here's": "Heres",
    "who's": "whos",
    "Who's": "Whos",
    "let's": "lets",
    "Let's": "Lets",
    "you're": "youre",
    "You're": "Youre",
    "they're": "theyre",
    "They're": "Theyre",
    "we're": "were",
    "We're": "Were",
}


def strip_apostrophes(text: str) -> str:
    """Convert contractions to ESL style — Lubo never uses apostrophes."""
    for contraction, replacement in _APOSTROPHE_MAP.items():
        text = text.replace(contraction, replacement)
    # Catch any remaining curly apostrophes
    text = text.replace("\u2019", "")  # right single quote
    text = text.replace("\u2018", "")  # left single quote
    # Fix broken backslash escapes from LLM (What\s → Whats, I\m → im)
    text = re.sub(r"\\s\b", "s", text)
    text = re.sub(r"\\m\b", "m", text)
    text = re.sub(r"\\t\b", "t", text)
    text = re.sub(r"\\re\b", "re", text)
    text = re.sub(r"\\ve\b", "ve", text)
    text = re.sub(r"\\ll\b", "ll", text)
    return text


def strip_json_wrapper(text: str) -> str:
    """If the text contains a JSON object with post_text, extract just the value."""
    stripped = text.strip()
    # Find "post_text" key first, then walk back to the opening {
    pt_idx = stripped.find('"post_text"')
    if pt_idx == -1:
        return text
    first = stripped.rfind("{", 0, pt_idx)
    if first == -1:
        return text
    last = stripped.rfind("}")
    if last <= first:
        return text
    candidate = stripped[first : last + 1]
    # LLM produces mixed escaping — try multiple strategies
    attempts = [
        candidate,
        candidate.replace("\n", "\\n").replace("\r", "\\r"),
    ]
    # Also try: fix broken backslash escapes (\\s → 's, \\n already escaped)
    fixed = candidate.replace("\n", "\\n").replace("\r", "\\r")
    fixed = fixed.replace("\\\\n", "\\n").replace("\\\\s", "'s").replace("\\\\t", "'t")
    attempts.append(fixed)
    for attempt_text in attempts:
        try:
            data = json.loads(attempt_text)
            if isinstance(data, dict) and "post_text" in data:
                return data["post_text"]
        except json.JSONDecodeError:
            continue
    # Last resort: regex extract the value between "post_text": " and the next unescaped "
    import re

    m = re.search(r'"post_text"\s*:\s*"(.*?)"(?:\s*,|\s*\})', candidate, re.DOTALL)
    if m:
        return m.group(1).replace("\\n", "\n").replace('\\"', '"')
    return text


def enforce_line_breaks(text: str) -> str:
    """Break up paragraphs longer than 3 sentences.

    Lubo's style: every 1-2 sentences gets its own line.
    """
    paragraphs = text.split("\n")
    result = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            result.append("")
            continue
        # Count sentences (rough: split on ". " or "? " or "! ")
        sentences = re.split(r"(?<=[.!?])\s+", para)
        if len(sentences) <= 3:
            result.append(para)
        else:
            # Break into chunks of 2 sentences
            for i in range(0, len(sentences), 2):
                chunk = " ".join(sentences[i : i + 2])
                result.append(chunk)
    return "\n".join(result)


def ensure_paragraph_spacing(text: str) -> str:
    """Insert blank lines between groups of text to create readable paragraphs.

    LinkedIn posts need visual breathing room. If there are more than 3
    consecutive non-empty lines without a blank line, insert one after
    every 2-3 lines. Preserves existing blank lines.
    """
    lines = text.split("\n")
    result: list[str] = []
    consecutive = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            # Already a blank line — reset counter
            result.append("")
            consecutive = 0
        else:
            consecutive += 1
            # After 3 consecutive non-empty lines, insert a blank line before this one
            if consecutive > 3:
                result.append("")
                consecutive = 1
            result.append(line)

    # Clean up: collapse 3+ blank lines into 2 (one visual break)
    cleaned: list[str] = []
    blank_count = 0
    for line in result:
        if line.strip() == "":
            blank_count += 1
            if blank_count <= 1:
                cleaned.append("")
        else:
            blank_count = 0
            cleaned.append(line)

    # Strip leading/trailing blank lines
    while cleaned and cleaned[0].strip() == "":
        cleaned.pop(0)
    while cleaned and cleaned[-1].strip() == "":
        cleaned.pop()

    return "\n".join(cleaned)


_FILLER_PATTERNS = [
    re.compile(r"As someone who[^.]*[.,]\s*", re.IGNORECASE),
    re.compile(r"Let me tell you[^.]*[.,]\s*", re.IGNORECASE),
    re.compile(r"Here'?s the thing[^.]*[.,]\s*", re.IGNORECASE),
    re.compile(r"The real story here[^.]*[.,]\s*", re.IGNORECASE),
    re.compile(r"As a data engineer[^.]*[.,]\s*", re.IGNORECASE),
    re.compile(r"As a solo builder[^.]*[.,]\s*", re.IGNORECASE),
    re.compile(r"Here'?s my take[^.]*[.:]\s*", re.IGNORECASE),
    re.compile(r"Key point:\s*", re.IGNORECASE),
    re.compile(r"My take:\s*", re.IGNORECASE),
    re.compile(r"The Bottom Line[^.]*[.:]\s*", re.IGNORECASE),
    re.compile(r"Real talk\.\s*", re.IGNORECASE),
]


def strip_filler_phrases(text: str) -> str:
    """Remove LinkedIn-influencer filler phrases."""
    for pattern in _FILLER_PATTERNS:
        text = pattern.sub("", text)
    # Clean up leftover whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_NEWS_ANCHOR_PATTERNS = [
    re.compile(r"^Hot AI news[^.]*[.!]\s*", re.IGNORECASE),
    re.compile(r"^Big Tech news[^.]*[.!]\s*", re.IGNORECASE),
    re.compile(r"^Big Tech drama[^.]*[.!]\s*", re.IGNORECASE),
    re.compile(r"^Breaking[:.!]\s*", re.IGNORECASE),
    re.compile(r"^Todays top story[^.]*[.!]\s*", re.IGNORECASE),
    re.compile(r"^Just saw this article[^.]*[.!]\s*", re.IGNORECASE),
    re.compile(r"^AI news[^.]*[.!]\s*", re.IGNORECASE),
    re.compile(r"^Tech Talk -\s*", re.IGNORECASE),
    re.compile(r"^Data engineering work is all about[^.]*[.!]\s*", re.IGNORECASE),
]


def strip_news_anchor_openings(text: str) -> str:
    """Remove news-anchor style openings that the LLM keeps using."""
    for pattern in _NEWS_ANCHOR_PATTERNS:
        text = pattern.sub("", text)
    return text.strip()


_SETEXT_UNDERLINE_RE = re.compile(r"^\s*[=\-_]{3,}\s*$")
_HEADER_RE = re.compile(r"^\s*#{1,6}\s*")
_STAR_BULLET_RE = re.compile(r"^(\s*)\*\s+")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_BOLD_US_RE = re.compile(r"__(.+?)__")
_ITALIC_RE = re.compile(r"\*([^*\n]+?)\*")
_CODE_RE = re.compile(r"`([^`\n]+?)`")


def strip_markdown(text: str) -> str:
    """Strip markdown the LLM adds — LinkedIn renders none of it.

    Removes bold/italic/code markers and header '#', drops setext underline lines
    (====, ----), and converts '* ' bullets to '- ' (Lubo's style).
    """
    lines = []
    for line in text.split("\n"):
        if _SETEXT_UNDERLINE_RE.match(line):
            continue
        line = _HEADER_RE.sub("", line)
        line = _STAR_BULLET_RE.sub(r"\1- ", line)
        lines.append(line)
    text = "\n".join(lines)
    text = _BOLD_RE.sub(r"\1", text)
    text = _BOLD_US_RE.sub(r"\1", text)
    text = _ITALIC_RE.sub(r"\1", text)
    text = _CODE_RE.sub(r"\1", text)
    return text.replace("**", "").replace("`", "")


_RULE_LEAK_RE = re.compile(
    r"\[[^\]]*(?:NO NUMBER|AS PER RULE|PLACEHOLDER|INSERT|NO DATA|TODO|COULD SAY)[^\]]*\]",
    re.IGNORECASE,
)
_META_LABEL_LINE_RE = re.compile(r"^\s*(linkedin post|post text|the post|output|caption)\s*:?\s*$", re.IGNORECASE)
_META_KV_LINE_RE = re.compile(r"^\s*(screenshot url|screenshot|hashtags?)\s*:.*$", re.IGNORECASE)


def strip_model_meta(text: str) -> str:
    """Remove model scaffolding: bracketed rule-leaks and label/meta lines.

    The 49B sometimes verbalizes prompt rules ("[NO NUMBER - AS PER RULES]") and
    emits structural labels ("LinkedIn Post", "Screenshot URL: null", "Hashtags: ...").
    """
    text = _RULE_LEAK_RE.sub("", text)
    kept = [ln for ln in text.split("\n") if not (_META_LABEL_LINE_RE.match(ln) or _META_KV_LINE_RE.match(ln))]
    text = "\n".join(kept)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_BRAND_RE = re.compile(r"\b[Ll][UuOo][Bb][Oo][Tt]\b(?!\.)")  # catches LuBot + the 'lobot' typo


def normalize_brand(text: str) -> str:
    """Fix brand casing to 'LuBot' (but leave the domain lubot.ai alone)."""
    return _BRAND_RE.sub("LuBot", text)


def deduplicate_hashtags(hashtags: list[str]) -> list[str]:
    """Remove duplicate hashtags, preserving order."""
    return list(dict.fromkeys(hashtags))


def limit_hashtags(hashtags: list[str], max_count: int = 5) -> list[str]:
    """Trim hashtag list to max_count."""
    return hashtags[:max_count]


def validate_post(text: str) -> tuple[bool, str]:
    """Check if a post meets quality rules. Returns (ok, reason)."""
    if len(text) < 400:
        ok, reason = False, f"Too short ({len(text)} chars, min 400)"
    elif len(text) > 1500:
        ok, reason = False, f"Too long ({len(text)} chars, max 1500)"
    elif "\u2014" in text or "\u2013" in text:
        ok, reason = False, "Contains em dash or en dash"
    elif text.strip().startswith("{") and '"post_text"' in text:
        ok, reason = False, "Contains JSON fragments"
    elif "?" not in text:
        ok, reason = False, "No question mark — needs engagement question"
    else:
        ok, reason = True, "ok"

    # Submit validation score to Langfuse
    try:
        get_client().score_current_trace(
            name="validation",
            value=1.0 if ok else 0.0,
            data_type="NUMERIC",
            comment=reason,
        )
    except Exception:
        logger.debug("Langfuse validation scoring failed", exc_info=True)

    return ok, reason


_NUMBER_RE = re.compile(r"\d[\d,]*\.?\d*")


def _extract_numbers(text: str) -> set[str]:
    """Numeric tokens from text, normalized (commas stripped, trailing dot removed)."""
    out: set[str] = set()
    for m in _NUMBER_RE.finditer(text):
        n = m.group().replace(",", "").rstrip(".")
        if n:
            out.add(n)
    return out


# A post number is treated as grounded if it lands within this relative distance of
# a real source number — covers honest rounding (7500 vs 7503.45) while staying strict
# enough to flag fabricated figures (9999.99, 45000, a percent that is 4%+ off).
_ROUNDING_TOLERANCE = 0.01


def numbers_grounded(post_text: str, source_text: str) -> tuple[bool, set[str]]:
    """Zero-BS check: every SIGNIFICANT number in the post must trace to the source data.

    'Significant' = has a decimal point or >= 3 digits (skips small ints like 1/2/3 and
    short counts). A number is grounded if it equals an allowed number or sits within
    _ROUNDING_TOLERANCE of one (tolerates honest rounding, e.g. 7500 vs 7503.45). Returns
    (ok, ungrounded). Catches fabricated prices/percentages/dollar amounts in market posts.
    """
    allowed = _extract_numbers(source_text)
    allowed_vals = [float(a) for a in allowed]
    ungrounded: set[str] = set()
    for n in _extract_numbers(post_text):
        digits = n.replace(".", "")
        if "." not in n and len(digits) < 3:
            continue  # trivial small integer — not a data claim
        if n in allowed:
            continue
        val = float(n)
        if any(abs(val - a) <= _ROUNDING_TOLERANCE * max(abs(a), 1.0) for a in allowed_vals):
            continue  # within rounding tolerance of a real number
        ungrounded.add(n)
    return (len(ungrounded) == 0, ungrounded)


def calculate_compliance_score(total_fixes: int) -> float:
    """Calculate LLM compliance score from number of fix categories triggered.

    Returns 1.0 for a perfect post (no fixes), 0.0 when all 6 categories needed fixes.
    Clamped to [0.0, 1.0].
    """
    if total_fixes <= 0:
        return 1.0
    return max(0.0, 1.0 - total_fixes / _MAX_FIX_CATEGORIES)


# Words that open an engagement question — used to safely add a dropped '?'.
_QUESTION_WORDS = frozenset(
    "what whats how hows why when where who which is are do does did would will "
    "could should can have has had am was were if".split()
)


def ensure_closing_question_mark(text: str) -> str:
    """Add a '?' to the closing line when it reads as a question but the model dropped it.

    Every post must end with an engagement question (validate_post enforces a '?').
    Reasoning models sometimes emit the question without the mark; if there is no '?'
    anywhere AND the last non-empty line opens with a question word, append one. A
    non-question close is left untouched (so we never fabricate a fake question).
    """
    if "?" in text:
        return text
    lines = text.rstrip("\n").split("\n")
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if not stripped:
            continue
        first = stripped.split()[0].lower().strip(",.!:;\"'")
        if first in _QUESTION_WORDS:
            lines[i] = lines[i].rstrip() + "?"
        break
    return "\n".join(lines)


@observe()
def process_post(text: str, hashtags: list[str]) -> tuple[str, list[str]]:
    """Full post-processing pipeline. Enforces all rules deterministically.

    Tracks which fix categories were triggered and reports compliance score to Langfuse.
    Return type is unchanged — (text, hashtags).
    """
    fixes: dict = {}

    prev = text
    text = strip_json_wrapper(text)
    fixes["json_wrapper_removed"] = text != prev

    # New-model cleanup (49B emits markdown + meta/rule-leaks). Applied, not scored,
    # so compliance categories stay stable.
    text = strip_markdown(text)
    text = strip_model_meta(text)
    text = normalize_brand(text)

    prev = text
    text = strip_dashes(text)
    fixes["dashes_stripped"] = text != prev

    prev = text
    text = strip_apostrophes(text)
    fixes["apostrophes_fixed"] = text != prev

    prev = text
    text = strip_filler_phrases(text)
    fixes["filler_phrases_removed"] = text != prev

    prev = text
    text = strip_news_anchor_openings(text)
    fixes["news_anchor_removed"] = text != prev

    prev = text
    text = enforce_line_breaks(text)
    fixes["line_breaks_enforced"] = text != prev

    prev = text
    text = ensure_paragraph_spacing(text)
    fixes["paragraph_spacing_added"] = text != prev

    # Safety net (applied, not scored): repair a dropped closing '?'.
    text = ensure_closing_question_mark(text)

    hashtags = deduplicate_hashtags(hashtags)
    hashtags = limit_hashtags(hashtags, max_count=5)

    # Calculate compliance score
    total = sum(1 for v in fixes.values() if v)
    fixes["total_fixes"] = total
    compliance = calculate_compliance_score(total)
    fixes["compliance_score"] = compliance

    # Report to Langfuse
    try:
        langfuse = get_client()
        langfuse.update_current_span(metadata=fixes)
        langfuse.score_current_trace(
            name="llm_compliance",
            value=compliance,
            data_type="NUMERIC",
            comment=f"Fixes: {total}/{_MAX_FIX_CATEGORIES} categories",
        )
    except Exception:
        logger.debug("Langfuse compliance reporting failed", exc_info=True)

    return text, hashtags
