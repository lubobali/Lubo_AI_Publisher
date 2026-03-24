"""AI post writer — builds prompts and calls NVIDIA Nemotron Ultra 253B."""

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from openai import AsyncOpenAI

from src.observability import get_client, observe
from src.scraper import ScrapedArticle

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

NVIDIA_MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"


@dataclass
class WriterResult:
    """Result of a successful post generation."""

    post_text: str
    screenshot_url: str | None
    hashtags: list[str] = field(default_factory=list)


def load_voice_rules() -> dict:
    """Load writing style rules from YAML config."""
    with open(CONFIG_DIR / "voice_rules.yaml") as f:
        return yaml.safe_load(f)


def load_voice_samples() -> str:
    """Load Lubo's real LinkedIn posts as style reference."""
    with open(TEMPLATES_DIR / "voice_samples.txt") as f:
        return f.read()


def build_system_prompt() -> str:
    """Assemble the full system prompt from voice rules + samples."""
    rules = load_voice_rules()
    samples = load_voice_samples()

    core_voice = "\n".join(f"- {r}" for r in rules["core_voice"])
    esl_grammar = "\n".join(f"- {r}" for r in rules.get("esl_grammar", []))
    writing_patterns = "\n".join(f"- {r}" for r in rules.get("writing_patterns", []))
    do_rules = "\n".join(f"- {r}" for r in rules["do"])
    do_not_rules = "\n".join(f"- {r}" for r in rules["do_not"])
    structure = rules["structure"]
    hashtag_rules = rules["hashtag_rules"]

    return f"""You are writing LinkedIn posts as Lubo Bali — a data engineer and AI systems builder.
Your job is to write ONE LinkedIn post that sounds exactly like Lubo wrote it himself.
Lubo is ESL (English as second language). His writing is casual, raw, imperfect — and thats what makes it authentic.

VOICE:
{core_voice}

ESL GRAMMAR (this is critical — Lubo NEVER uses apostrophes):
{esl_grammar}

WRITING PATTERNS (follow these exactly):
{writing_patterns}

STRUCTURE:
- First {structure["hook_lines"]} lines MUST be a catchy hook (this is what people see before "see more")
- Paragraphs: {structure["paragraph_sentences"]} sentences each
- Total length: {structure["length_chars_min"]}-{structure["length_chars_max"]} characters
- Ending: {structure["ending"]}

DO:
{do_rules}

DO NOT:
{do_not_rules}

IMPORTANT: Always say "I", never say "we". Lubo is a solo builder.

CRITICAL: Do NOT invent personal experiences, experiments, or results.
You may comment on articles ("this is wild", "I've been watching this space")
but NEVER fabricate things Lubo did, built, or measured unless the article is specifically about LuBot.

HASHTAGS:
- {hashtag_rules["min_count"]}-{hashtag_rules["max_count"]} hashtags at the very end of the post
- Mix broad and specific: {hashtag_rules["mix"]}
- Rotate hashtags — dont use exact same set every post

RESPONSE FORMAT:
You MUST respond with valid JSON only. No text before or after. Format:
{{"post_text": "the full post text here", "screenshot_url": "https://url-for-screenshot-or-null", "hashtags": ["#Tag1", "#Tag2", "#Tag3"]}}

Do NOT include hashtags inside post_text. Put them only in the hashtags array.

STYLE REFERENCE — these are Lubo's real posts. Match this voice exactly:

{samples}"""


def build_user_prompt(
    topic_name: str,
    topic_description: str,
    articles: list[ScrapedArticle],
    performance_context: str | None = None,
) -> str:
    """Build the user message with today's topic + scraped articles."""
    rules = load_voice_rules()

    prompt_parts = [
        f"Write a LinkedIn post for today's topic: {topic_name}",
        f"Topic description: {topic_description}",
    ]

    # Add topic-specific rules if they exist
    topic_key = topic_name.lower().replace(" ", "_")
    topic_specific = rules.get("topic_specific", {})
    if topic_key in topic_specific:
        specific_rules = topic_specific[topic_key]
        rules_text = "\n".join(f"- {r}" for r in specific_rules)
        prompt_parts.append(f"\nSPECIAL RULES FOR THIS TOPIC:\n{rules_text}")

    # Add LuBot-specific context for My Agent posts — grounded in real features
    if topic_key == "my_agent":
        features = rules.get("my_agent_features", [])
        if features:
            features_text = "\n".join(f"  - {f}" for f in features)
            prompt_parts.append(
                f"\nThis is a LuBot.ai marketing post. "
                f"ONLY use features from this list — do NOT invent features:\n{features_text}\n\n"
                f"Pick ONE feature and write a post about it. "
                f"Compare to ChatGPT or other tools. Show what makes LuBot different. "
                f"Use real numbers and real examples from the feature list above."
            )

    # Add scraped articles as context
    if articles:
        prompt_parts.append("\nHere are today's top articles for inspiration:")
        for i, article in enumerate(articles[:5], 1):
            prompt_parts.append(
                f"\n{i}. {article.title}\n"
                f"   URL: {article.url}\n"
                f"   Source: {article.source}\n"
                f"   Summary: {article.summary}"
            )
        prompt_parts.append(
            "\nPick the most interesting angle from these articles. "
            "Do NOT copy-paste. Write in Lubos voice with a personal take. "
            "CRITICAL: Any numbers you use MUST come from the article above. "
            "Do NOT invent line counts, test counts, percentages, dollar amounts, "
            "or performance metrics. If the article has no numbers, dont use numbers."
        )
    else:
        prompt_parts.append(
            "\nNo articles scraped today. Write from general knowledge "
            "about this topic, drawing on Lubo's personal experience."
        )

    # Add performance feedback if available
    if performance_context:
        prompt_parts.append(f"\nPERFORMANCE DATA:\n{performance_context}")

    prompt_parts.append(
        "\nSuggest a screenshot_url — a webpage that would make "
        "a good visual for this post (or null if not applicable)."
    )

    return "\n".join(prompt_parts)


def _strip_trailing_hashtags(text: str) -> str:
    """Remove trailing lines that are only hashtags."""
    lines = text.rstrip().split("\n")
    while lines:
        stripped = lines[-1].strip()
        if stripped and all(word.startswith("#") for word in stripped.split()):
            lines.pop()
        elif not stripped:
            lines.pop()  # Remove trailing blank lines too
        else:
            break
    return "\n".join(lines).rstrip()


def _try_parse_json(text: str) -> dict | None:
    """Try to extract a JSON object with post_text from text.

    Handles: clean JSON, JSON in markdown blocks, JSON with raw newlines,
    and JSON embedded in chain-of-thought reasoning text.
    """
    # Try direct parse
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "post_text" in data:
            return data
    except json.JSONDecodeError:
        pass

    # Find JSON substring containing "post_text"
    # Look for the { that precedes "post_text" — skip random { from thinking text
    idx = text.find('"post_text"')
    if idx == -1:
        return None
    # Walk backwards to find the opening {
    first_brace = text.rfind("{", 0, idx)
    if first_brace == -1:
        return None
    last_brace = text.rfind("}")
    if last_brace <= first_brace:
        return None

    candidate = text[first_brace : last_brace + 1]
    # Try parsing as-is, then with escaped newlines (253B puts raw \n in JSON strings)
    for attempt in [candidate, candidate.replace("\n", "\\n").replace("\r", "\\r")]:
        try:
            data = json.loads(attempt)
            if isinstance(data, dict) and "post_text" in data:
                return data
        except json.JSONDecodeError:
            continue

    return None


def _score_parse_quality(score: float, method: str) -> None:
    """Submit parse_quality score to Langfuse."""
    try:
        get_client().score_current_trace(
            name="parse_quality",
            value=score,
            data_type="NUMERIC",
            comment=f"Method: {method}",
        )
    except Exception:
        logger.debug("Langfuse parse_quality scoring failed", exc_info=True)


def parse_response(raw_text: str) -> WriterResult | None:
    """Parse LLM response into WriterResult. Returns None if unparseable."""
    text = raw_text.strip()

    # Strip markdown code block if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    data = _try_parse_json(text)

    if data is None:
        # Last resort: treat plain text as the post itself
        result = _parse_plain_text(text)
        _score_parse_quality(0.3 if result else 0.0, "plain_text" if result else "failed")
        return result

    post_text = _strip_trailing_hashtags(data.get("post_text", "").strip())
    if not post_text:
        logger.warning("LLM response has empty post_text")
        _score_parse_quality(0.0, "empty_post_text")
        return None

    # Normalize screenshot_url: LLM often returns "null" string instead of null
    screenshot_url = data.get("screenshot_url")
    if isinstance(screenshot_url, str) and screenshot_url.strip().lower() in ("null", "none", ""):
        screenshot_url = None

    _score_parse_quality(1.0, "json")
    return WriterResult(
        post_text=post_text,
        screenshot_url=screenshot_url,
        hashtags=data.get("hashtags", []),
    )


def _parse_plain_text(text: str) -> WriterResult | None:
    """Extract post from plain text when model ignores JSON format.

    Treats the entire text as post_text. Extracts hashtags if present.
    Rejects text shorter than 50 chars (likely garbage).
    """
    if len(text.strip()) < 50:
        logger.warning("Plain text too short to be a post: %s", text[:100])
        return None

    # Extract hashtags from the text (deduplicated, order-preserving)
    hashtags = list(dict.fromkeys(re.findall(r"#\w+", text)))

    # Remove trailing hashtag lines from the post body
    post_text = _strip_trailing_hashtags(text.strip())

    logger.info("Parsed plain text response as post (%d chars, %d hashtags)", len(post_text), len(hashtags))
    return WriterResult(post_text=post_text, screenshot_url=None, hashtags=hashtags)


def hash_prompt(text: str) -> str:
    """Generate an 8-char hex hash of a prompt for version tracking."""
    return hashlib.md5(text.encode()).hexdigest()[:8]


def get_llm_client() -> AsyncOpenAI:
    """Create an AsyncOpenAI client configured for NVIDIA NIM API."""
    return AsyncOpenAI(
        base_url=NVIDIA_BASE_URL,
        api_key=os.environ.get("NVIDIA_API_KEY", ""),
        max_retries=5,
        timeout=120.0,
    )


@observe(as_type="generation")
async def write_post(
    topic_name: str,
    topic_description: str,
    articles: list[ScrapedArticle],
    performance_context: str | None = None,
) -> WriterResult | None:
    """Generate a LinkedIn post using NVIDIA Nemotron.

    Returns WriterResult on success, None on failure.
    """
    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(
        topic_name=topic_name,
        topic_description=topic_description,
        articles=articles,
        performance_context=performance_context,
    )

    client = get_llm_client()

    try:
        response = await client.chat.completions.create(
            model=NVIDIA_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.8,
            max_tokens=2000,
        )

        # Report generation metadata to Langfuse
        try:
            usage = response.usage
            get_client().update_current_generation(
                model=response.model or NVIDIA_MODEL,
                model_parameters={"temperature": 0.8, "max_tokens": 2000},
                usage_details={
                    "input": usage.prompt_tokens if usage else 0,
                    "output": usage.completion_tokens if usage else 0,
                },
                metadata={"topic": topic_name, "prompt_version": hash_prompt(system_prompt)},
            )
        except Exception:
            logger.debug("Langfuse generation update failed", exc_info=True)

        msg = response.choices[0].message
        raw_text = msg.content
        # Nemotron 253B sometimes puts the answer in reasoning_content
        if not raw_text:
            raw_text = getattr(msg, "reasoning_content", None)
        if not raw_text:
            logger.warning("LLM returned empty content")
            return None
        logger.info("LLM response received (%d chars)", len(raw_text))

        return parse_response(raw_text)

    except Exception as e:
        logger.warning("LLM call failed: %s", e)
        return None
