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


def calculate_compliance_score(total_fixes: int) -> float:
    """Calculate LLM compliance score from number of fix categories triggered.

    Returns 1.0 for a perfect post (no fixes), 0.0 when all 6 categories needed fixes.
    Clamped to [0.0, 1.0].
    """
    if total_fixes <= 0:
        return 1.0
    return max(0.0, 1.0 - total_fixes / _MAX_FIX_CATEGORIES)


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
