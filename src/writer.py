"""AI post writer — builds prompts and calls an NVIDIA Nemotron model."""

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

# Env-overridable so vendor rotations are a 1-line .env change (the old 253B was
# retired ~May 6, 2026). Default = Nemotron 3 Ultra 550B (NVIDIA's flagship open
# model, free on the NIM endpoint). It writes clean, on-voice, low-hallucination
# posts — verified far better than the 49B. Falls back to 49B if Ultra is ever
# rate-limited (NVIDIA_LLM_MODEL=nvidia/llama-3.3-nemotron-super-49b-v1).
NVIDIA_MODEL = os.getenv("NVIDIA_LLM_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

# OpenRouter fallback — free NIM has a rate-limit cap shared across LuBot apps on
# the same key. When NIM 429s/errors, we retry on OpenRouter so a daily post never
# silently fails. Only active when OPENROUTER_API_KEY is set. Paid (no rate limit),
# but the publisher is low-volume so this costs pennies and only when NIM is down.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b")

# Nemotron-3 are REASONING models: they spend output tokens on hidden reasoning before
# the answer. A small cap truncates them mid-reasoning -> EMPTY content (verified Jun 22:
# max_tokens=2000 -> finish=length, content=0; ~3.5k tokens went to reasoning). Give
# generous headroom so the answer always lands. And 550B reasoning is slow, so a 120s
# timeout was tripping ("Request timed out"). (Writer-stability fix, P9, Jun 22.)
MAX_OUTPUT_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "8000"))
REQUEST_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "300"))


@dataclass
class WriterResult:
    """Result of a successful post generation."""

    post_text: str
    screenshot_url: str | None
    hashtags: list[str] = field(default_factory=list)
    card_headline: str = ""  # short pull-quote for the insight card (opinion categories)


@dataclass
class CarouselResult:
    """A generated swipeable CAROUSEL (Phase 2.21): the feed CAPTION (post_text that sits above
    the deck) plus the slide content — a scroll-stopping HOOK (slide 1), 3-5 short POINT slides
    (one idea each), and a soft CTA slide. cards.build_carousel_slides() renders these to PNGs."""

    caption: str
    hook: str
    points: list[str]
    cta: str
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

TRUTH (this is the most important rule — one fake detail destroys credibility with recruiters):
- Never invent a specific number — row counts, percentages, durations, ms/seconds, dollar amounts, user counts, dates, version numbers. If you dont have a REAL number, say it in words ("the table got huge", "queries crawled", "it took forever"). A made up number is a lie.
- Never name a specific tool, library, framework, or company you didnt actually use (no Databricks, Snowflake, Kafka, Airflow, etc.) unless it appears in the material above. Inventing a tech stack is a lie.
- Do NOT invent personal experiences, experiments, or results. The ONLY facts/numbers you may use are ones that appear in the material above.
- This covers facts you THINK you know about LuBot from training too — its table count, worker count, tool count, model count, line-of-code count. If an exact number is not in the material above, do NOT state it. Say it in words ("a bunch of tables", "lots of background workers") instead. A stale or guessed number a recruiter can disprove is worse than no number.
- Personal story vs opinion: do NOT claim you personally did a specific thing ("I migrated to X", "I moved the workload to Y", "what worked for me was Z") unless it is in the material above. You CAN share general expertise as OPINION ("when a table gets huge, the usual move is...", "id reach for..."). General knowledge framed as opinion = honest. General knowledge framed as a specific thing you did = a lie.
- You may react and give opinions ("this is wild", "I have watched this space") — that is not fabrication.
- NEVER claim you personally consumed the source. Do NOT say "I just listened to / watched / read / attended" a podcast, video, article, episode, or talk. The source material was handed to you — Lubo did NOT necessarily listen to or read it. Instead CITE it (name the people/show) and give Lubo's take, or state the insight as his opinion. WRONG: "I just listened to the Lex Fridman episode with X and Y." RIGHT: "X and Y broke this down on Lex Fridman — the wild part is..." or just "FFmpeg runs every video you watch and nobody thinks about it." Same value, but you never fake an experience Lubo didnt have.

PLAIN TEXT ONLY: No markdown. No ** bold **, no # headers, no backticks, no "* " bullets (use "- " if you must list). LinkedIn renders markdown as literal symbols and it looks broken.

PLAIN HUMAN TYPING: Write like a real person on a normal keyboard. NO em-dashes (the long "—"), NO en-dashes, NO arrows (no "->", no unicode arrows), NO fancy/special unicode symbols or smart quotes. Use only a normal hyphen (-), comma, period, colon, and regular letters. This applies to post_text AND card_headline.

HASHTAGS:
- {hashtag_rules["min_count"]}-{hashtag_rules["max_count"]} hashtags at the very end of the post
- Mix broad and specific: {hashtag_rules["mix"]}
- Rotate hashtags — dont use exact same set every post

RESPONSE FORMAT:
You MUST respond with valid JSON only. No text before or after. Format:
{{"post_text": "the full post text here", "screenshot_url": "https://url-for-screenshot-or-null", "hashtags": ["#Tag1", "#Tag2", "#Tag3"], "card_headline": "4-8 word punchy pull-quote of your core point"}}

Do NOT include hashtags inside post_text. Put them only in the hashtags array.
card_headline: a short, punchy 4-8 word version of your single core point — for the post's image card. Plain words, no hashtags, no quotes, no markdown. It should be able to stand alone as a strong one-liner.

STYLE REFERENCE — these are Lubo's real posts. Match this voice exactly:

{samples}"""


# Carousel override — appended AFTER the full voice/truth base so a carousel reuses every voice,
# ESL, and TRUTH rule; only the STRUCTURE and RESPONSE FORMAT change. The final format wins.
CAROUSEL_FORMAT = """============================================================
CAROUSEL MODE — this OVERRIDES the single-post STRUCTURE and RESPONSE FORMAT above.
============================================================
You are NOT writing one paragraph post. You are writing a SWIPEABLE CAROUSEL — a set of big-font
slides people swipe through on LinkedIn. Same voice, same ESL grammar (no apostrophes), same TRUTH
rules as above. A carousel lives and dies on the first slide, so the hook has to stop the scroll.

Build these pieces:
- HOOK (slide 1): ONE scroll-stopping line, max about 9 words. Bold claim, sharp question, or a
  surprising truth. This is the whole game — a weak hook means nobody swipes.
- POINTS (4 to 5 slides): ONE idea per slide, ONE short line each (max about 14 words). Real value,
  a concrete insight, no filler and no repeating the hook. Each must stand on its own. Do NOT number
  them yourself (the slide design adds the number). The final deck also includes a data/proof card
  slide, so 4 to 5 points lands a tight ~7-slide carousel. No colons-as-labels, no "Tip 1:".
- CTA (last slide): ONE short line that softly points the reader to LuBot or to follow. Not salesy.
- CAPTION: the text that sits ABOVE the carousel in the feed — 2 to 4 short lines in Lubo voice.
  Open with the hook or a related thought, add one line of context, nudge them to swipe. No hashtags
  inside it.

Slide rules: plain words only. No markdown, no emojis, no hashtags on slides, no quotes, no arrows,
no em-dashes. Keep every slide SHORT — long lines get cut off on a big-font slide. TRUTH rules fully
apply: no invented numbers, tools, or personal claims; points are insight and opinion, never a faked
experience.

RESPONSE FORMAT (respond with valid JSON ONLY, nothing before or after):
{"caption": "the feed caption in Lubo voice", "hook": "the slide 1 hook line", "points": ["point one", "point two", "point three"], "cta": "the closing line", "hashtags": ["#Tag1", "#Tag2", "#Tag3"]}"""


def build_carousel_system_prompt() -> str:
    """The full voice/truth system prompt + the CAROUSEL_FORMAT override (Phase 2.21)."""
    return build_system_prompt() + "\n\n" + CAROUSEL_FORMAT


def build_user_prompt(
    topic_name: str,
    topic_description: str,
    articles: list[ScrapedArticle],
    performance_context: str | None = None,
    book_concepts: list[str] | None = None,
    podcast_context: str | None = None,
    recent_posts: list[str] | None = None,
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
                f"Use ONLY facts from the feature list above, and use any number EXACTLY as "
                f"written — never change a digit (if it says 34 tables, never write 36). "
                f"If a number is not in the list, do not state one. "
                f"Do NOT invent a fake user scenario with made up data and dates "
                f"(no 'uploaded sales data in January, asked in March'). "
                f"Remember: LuBot is 100% NVIDIA — never say it uses GPT, Claude, or Gemini."
            )

    # Git-based My Agent Build posts — grounded in real commits
    if topic_key == "my_agent_build":
        prompt_parts.append(
            "\nThis is a BUILD LOG post. The summary below has ONE specific feature from Lubos git log.\n\n"
            "RULES:\n"
            "- Write ONLY about the ONE feature listed under 'THIS WEEK I BUILT'\n"
            "- Use the EXACT numbers from the summary (+lines/-lines, file count)\n"
            "- Do NOT mention other commits or features — they are just context for you\n"
            "- Tell the story: what was the problem, what did Lubo do, what was the result\n"
            "- Always say 'I', never 'we' or 'us' — Lubo is solo\n"
            "- Include a real struggle or surprise from the build process\n"
            "- NO cliches like 'blood and sweat', 'one place to rule them all', 'because AI is hard'\n"
            "- This should sound like a developer texting a friend about their week, not a blog post\n\n"
            "FORMATTING (critical for LinkedIn readability):\n"
            "- Use BLANK LINES between paragraphs. Every 2-3 lines of text, add a blank line\n"
            "- When listing items, put each on its own line starting with '- ' (short dash)\n"
            "- The hook (first 2 lines) must stand alone, then a blank line\n"
            "- Numbers/stats get their own short paragraph\n"
            "- The closing question gets its own paragraph at the end\n"
            "- Think: hook → stats → problem → solution → surprise → question. Each section separated by blank line\n\n"
            "ANTI-HALLUCINATION (critical):\n"
            "- The ONLY numbers you may use are: the +lines/-lines and file count from the summary\n"
            "- Do NOT invent performance metrics (ms, seconds, latency, speed improvements)\n"
            "- Do NOT invent percentages, dollar amounts, or user counts\n"
            "- Do NOT fabricate what specific code does — just describe what the commit message says\n"
            "- Do NOT attribute advice or quotes to anyone\n"
            "- If you dont know the details, keep it vague ('it was slow' not 'it took 1.2 seconds')\n"
            "- ZERO TOLERANCE: if a number is not in the summary above, you CANNOT use it in the post. "
            "No '80%', no '10x', no 'X seconds'. The ONLY numbers allowed are lines added, lines deleted, "
            "net lines, and file count from the summary. Everything else must be described in words, not numbers."
        )

    # Building-in-public weekly stats — grounded in real WakaTime numbers
    if topic_key == "building_in_public":
        prompt_parts.append(
            "\nThis is a BUILDING IN PUBLIC post about Lubos real coding week. "
            "The summary below has REAL stats from his WakaTime tracker.\n\n"
            "RULES:\n"
            "- Open with YOUR reaction to the week, not the raw numbers\n"
            "- Use the EXACT numbers from the summary (hours, percentages, sessions, tokens, cost), "
            "INCLUDING decimals — never round (write 81.6h not '81 hours', 54.9h not '55 hours')\n"
            "- Lead with the most striking specific number — people love specifics\n"
            "- Be honest: heavy week or light week, say it straight\n"
            "- Always say 'I', never 'we' — Lubo is solo\n"
            "- This is about building WITH AI coding agents — own that angle, its the edge\n"
            "- NO hype, NO ad copy, NO 'grind' or 'blood and sweat' cliches\n\n"
            "FORMATTING (LinkedIn readability):\n"
            "- BLANK LINES between paragraphs\n"
            "- Stats get their own short lines\n"
            "- The closing reflection/question gets its own paragraph\n\n"
            "ANTI-HALLUCINATION:\n"
            "- The ONLY numbers allowed are the ones in the WakaTime summary above\n"
            "- Do NOT invent metrics, percentages, comparisons, or costs not in the data"
        )

    # Stock Talk / Market Pulse — grounded in real yfinance market numbers
    if topic_key == "market_pulse":
        prompt_parts.append(
            "\nThis is a MARKET PULSE post — a seasoned broker's quick read on the week, in "
            "Lubo's casual ESL voice. The summary below has REAL market numbers.\n\n"
            "VOICE: write like a pro who has watched markets for years — plain-spoken, specific, "
            "evidence-based, calm, zero hype. Perspective, not a hot tip.\n\n"
            "RULES:\n"
            "- VARY THE ANGLE every week. Do NOT reuse a standard opening, the same principles, or "
            "the same closing. Rotate stance — some weeks calm/grounded, some weeks sharp/skeptical "
            "(question the narrative, real-or-froth). Pick a different lens (breadth, rotation, "
            "volatility, behavior, data-vs-feelings, a historical parallel). Never echo a past post\n"
            "- Open with a fresh observation in your words, NOT the raw index number\n"
            "- Be balanced: name the upside AND the risk, never one-sided\n"
            "- NOT financial advice: no buy/sell, no targets, no predictions, no 'you should'. A pro gives perspective, not tips\n"
            "- Reference LuBot Stock mode naturally and DIFFERENTLY (or not at all) — never the same 'built it to remove emotion' line\n"
            "- Use at most ONE durable truth, said fresh — never a fixed list of three\n"
            "- Always 'I', never 'we'. Short lines, blank lines\n"
            "- No hype: no 'to the moon', 'load up', 'next 10x', 'buy the dip', 'easy money'\n"
            "- End with ONE fresh question, ending with a question mark (?)\n\n"
            "FORMATTING: blank lines between paragraphs; numbers on their own short lines; closing question its own paragraph.\n\n"
            "ZERO-BS RULE (critical): EVERY number in your post must come from the summary above — "
            "the index closes and weekly % moves, exactly as written. Invent NO other number: no made-up "
            "price, percent, dollar amount, prediction, or target. If you have no real number for a point, "
            "use words, not a fabricated figure.\n"
            "DIGITS RULE: write numbers as DIGITS exactly as in the data (6.4%, 7,500.58, 70.79). "
            "NEVER spell a number out in words (never 'six point four percent' or 'seventy point seventy nine'). "
            "Digits only.\n"
            "NO DERIVED NUMBERS: state only the exact figures from the data. Do NOT compute or "
            "invent new ones — no gaps, spreads, sums, differences, ratios, or averages between "
            "them (e.g. never 'the 2.7 point gap'). Describe a divergence in words, not a new number."
        )

    # Stock Talk / Investing Principle — calm evergreen wisdom, not a market recap
    if topic_key == "investing_principle":
        prompt_parts.append(
            "\nThis is a STOCK TALK / INVESTING PRINCIPLE post — calm, long-term, evergreen "
            "wisdom from someone who BUILT an AI stock advisor (LuBot Stock mode). NOT a market recap.\n\n"
            "RULES:\n"
            "- Open with a principle or YOUR reaction, not a headline\n"
            "- Share HOW you think about investing over years, not days\n"
            "- NOT financial advice: no buy/sell, no targets, no predictions, no 'you should', no tickers\n"
            "- Use NO numbers at all — not even illustrative ones. Write 'a small move' not 'a 2 percent move'. No invented returns, dates, or personal trades\n"
            "- Never name or quote a book, blog, author, or podcast — make the idea YOUR take\n"
            "- Always 'I', never 'we'. Short lines, blank lines between thoughts\n"
            "- No hype: no 'to the moon', 'load up', 'next 10x', 'easy money', 'get rich'\n"
            "- One honest or self-deprecating beat is welcome\n"
            "- End with ONE clear question to the audience, ending with a question mark (?)\n\n"
            "ANTI-HALLUCINATION (critical):\n"
            "- Invent NO numbers, percentages, prices, returns, or dates\n"
            "- Do NOT fabricate personal trading results or holdings"
        )

    # Biohacker / longevity — Lubo's lived philosophy (memory project_biohacker_brief)
    if topic_key == "biohacker":
        prompt_parts.append(
            "\nThis is a BIOHACKER / LONGEVITY post. Below is everything Lubo believes about health "
            "and a distilled takeaway from a longevity expert. This is his WORLDVIEW and KNOWLEDGE BASE "
            "to think from - it is NOT a checklist to recite and NOT a template to fill in.\n\n"
            "HOW TO USE THIS (read carefully):\n"
            "- Write ONE post about ONE idea. Go deep on a single thing, do not dump the whole philosophy.\n"
            "- Each post should pick a DIFFERENT facet: maybe a thing to stop, maybe one free habit, maybe a "
            "single test, maybe the age idea, maybe a mindset shift, maybe a myth you bust. Rotate. Never the same shape twice.\n"
            "- Internalize these beliefs and say them YOUR way, fresh, like a real person who lives this - "
            "do NOT copy these sentences or list them out. If a post reads like the brief, you failed.\n"
            "- Use only what serves today's one idea. Leave the rest out. Less is more.\n\n"
            "LUBO'S PHILOSOPHY (his real beliefs - the base to think from):\n"
            "- Biohacking = optimizing the WHOLE body as one system: food, environment, stress, work, the "
            "people around you, sleep, light, movement, time outdoors, mind. NOT just supplements.\n"
            "- Most disease comes from bad habits, not genetics. The biggest wins come from REMOVING harm "
            "(seed oils, ultra-processed food, plastics, toxins) before adding anything.\n"
            "- Everyone can start FREE - knowledge, not money, is the barrier. Money buys precision and speed, "
            "not the basics. So serve BOTH the no-money and the money audience.\n"
            "- The age idea: chronological age is fixed, biological age is not - you bend it with daily habits. "
            "Epigenetic clocks just MEASURE it. Empowering, not preachy.\n"
            "- Tone: generous, practical, helping a friend. No hype, no preaching, no selling. Flag solid science vs marketing.\n\n"
            "FACTS YOU MAY DRAW ON (only if they serve today's one idea - do not list them):\n"
            "- Free ways to gauge aging: resting heart rate, grip strength, waist-to-height, recovery speed, VO2max.\n"
            "- Cheap bloodwork: ApoB, hs-CRP, fasting insulin, HbA1c, lipids.\n"
            "- Premium epigenetic clocks: TruDiagnostic TruAge, GrimAge, DunedinPACE (measure biological age + aging speed).\n\n"
            "LUBO'S REAL CREDIBILITY (true - use SPARINGLY and only when it fits, never as a crutch):\n"
            "- He has lived this 5+ years; he is 46 and feels/looks about 35. This is powerful BUT do not open "
            "with it every time and do not repeat it across posts. If a recent post already used his age or "
            "years, do NOT use them again - find another way to show he lives it, or skip it entirely.\n"
            "- Never invent a tested age number ('my biological age is 32') unless it is a REAL result. "
            "'feel and look 35' is subjective and fine.\n\n"
            "HONEST FRAMING:\n"
            "- You MAY cite the expert/show by name ('Asprey made the case that...', 'Saladino argues...'). Citing is credible.\n"
            "- Do NOT claim Lubo listened to this exact episode (the pipeline picked it, not him). "
            "'I have spent years on this' is true; 'I just listened to this episode' is not.\n\n"
            "CRAFT:\n"
            "- Always 'I', never 'we'. Short lines, blank lines between thoughts. End with ONE real question (?).\n"
            "- Use NO numbers, studies, or dosages that are not in the material above. Do NOT fabricate a personal experiment.\n\n"
            "MARKETING (soft, honest): LuBot has no biohacking feature yet (only stock, website, my-files modes). "
            "Do NOT market a LuBot biohacking product. Any mention stays soft and general. The job is value and authority, not a sell."
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

    # Book knowledge (RAG) — LOWEST priority. Background only, never the topic.
    # Guardrails per docs/LuBot_Publisher_Plan.txt VOICE & POSITIONING GUARDRAILS.
    if book_concepts:
        concepts_text = "\n".join(f"- {c}" for c in book_concepts)
        prompt_parts.append(
            "\nBACKGROUND YOU HAPPEN TO KNOW (use ONLY to sharpen your own opinion, "
            "in your own words — this is NOT the topic):\n"
            f"{concepts_text}\n"
            "RULES for this background:\n"
            "- Do NOT name or cite any book, author, or title\n"
            "- Do NOT paraphrase or summarize it — no textbook voice\n"
            "- Do NOT claim you built, applied, or benchmarked something unless you actually did\n"
            "- Open with YOUR reaction, never with this background\n"
            "- If it does not fit naturally, ignore it completely"
        )

    # Podcast angle (Phase 2.10b) — distilled bullets of what market commentators are
    # debating. The SPARK for Lubo's take, never the source. Numbers stay yfinance-only.
    if podcast_context:
        prompt_parts.append(
            "\nRECENT MARKET THINKING (what smart investors are debating right now — "
            "use ONLY to shape YOUR angle, in your own words):\n"
            f"{podcast_context}\n"
            "RULES for this:\n"
            "- This is the SPARK for your take, not the topic. React to it — agree, push back, or riff\n"
            "- NEVER name or quote a podcast, show, host, or person. Never say 'I heard' or 'on a podcast'\n"
            "- Do NOT parrot their analyst wording or jargon — say it in your own casual ESL voice\n"
            "- Treat it as the general mood/debate, NOT 'this exact week'\n"
            "- Take NO number from this. Every number comes ONLY from the market data above\n"
            "- If a point does not fit naturally, ignore it"
        )

    # Anti-repeat memory — your own recent posts in this category. The single most
    # important rule for sounding human: never run the same play twice.
    if recent_posts:
        recent_text = "\n\n".join(f"PAST POST {i}:\n{p.strip()[:900]}" for i, p in enumerate(recent_posts, 1))
        prompt_parts.append(
            "\nYOUR RECENT POSTS IN THIS CATEGORY (read them, then deliberately do something DIFFERENT):\n"
            f"{recent_text}\n"
            "ANTI-REPEAT RULES (critical — repetition is the fastest way to look like a bot):\n"
            "- Do NOT reuse the same opening line or hook pattern as any post above\n"
            "- Do NOT repeat the same personal lines, catchphrases, or signature sentences "
            "(e.g. if a recent post already said your age or years of experience, do NOT say it again this time)\n"
            "- Do NOT lead with the same idea, the same example, or the same structure\n"
            "- Pick a genuinely different angle, a different facet of the topic, a different emotion\n"
            "- Vary the closing question — never echo a question you already asked above\n"
            "- These are your past work, not a template. Move the conversation forward, do not restate it"
        )

    prompt_parts.append(
        "\nSuggest a screenshot_url — a webpage that would make "
        "a good visual for this post (or null if not applicable)."
    )

    return "\n".join(prompt_parts)


def build_carousel_user_prompt(
    topic_name: str,
    topic_description: str,
    articles: list[ScrapedArticle],
    performance_context: str | None = None,
    book_concepts: list[str] | None = None,
    podcast_context: str | None = None,
    recent_posts: list[str] | None = None,
) -> str:
    """Reuse the full single-post material (topic rules, articles, concepts, anti-repeat) then
    override the closing ask: turn it into a CAROUSEL instead of one paragraph post."""
    base = build_user_prompt(
        topic_name=topic_name,
        topic_description=topic_description,
        articles=articles,
        performance_context=performance_context,
        book_concepts=book_concepts,
        podcast_context=podcast_context,
        recent_posts=recent_posts,
    )
    return base + (
        "\n\nNOW MAKE THIS A CAROUSEL, not a single paragraph post (ignore the screenshot_url ask). "
        "Distill the topic into one scroll-stopping hook, 3 to 5 short point slides (one idea each, "
        "real value, no filler), a soft CTA to LuBot, and a feed caption. Respond in the CAROUSEL JSON "
        "format only."
    )


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
        card_headline=(data.get("card_headline") or "").strip(),
    )


def _extract_json_block(text: str, anchor: str) -> dict | None:
    """Extract a JSON object that contains `"<anchor>"` — direct parse first, then the brace
    span around the anchor (skips chain-of-thought), with a raw-newline escape retry. Generalizes
    the post_text extractor for carousel keys (hook/points)."""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    idx = text.find(f'"{anchor}"')
    if idx == -1:
        return None
    first_brace = text.rfind("{", 0, idx)
    last_brace = text.rfind("}")
    if first_brace == -1 or last_brace <= first_brace:
        return None

    candidate = text[first_brace : last_brace + 1]
    for attempt in (candidate, candidate.replace("\n", "\\n").replace("\r", "\\r")):
        try:
            data = json.loads(attempt)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


def parse_carousel_response(raw_text: str) -> CarouselResult | None:
    """Parse an LLM carousel response into a CarouselResult. Requires a hook and >=2 points;
    returns None otherwise (a carousel with no real points is not worth posting)."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

    data = _extract_json_block(text, "points")
    if data is None:
        logger.warning("Carousel response was not parseable JSON")
        return None

    hook = " ".join(str(data.get("hook", "")).split())
    points = [" ".join(str(p).split()) for p in (data.get("points") or []) if str(p).strip()]
    if not hook or len(points) < 2:
        logger.warning("Carousel response missing a hook or enough points (hook=%r, points=%d)", hook, len(points))
        return None

    points = points[:5]  # keep the deck tight — 5 slides max
    caption = _strip_trailing_hashtags(str(data.get("caption", "")).strip()) or hook
    cta = " ".join(str(data.get("cta", "")).split()) or "Try LuBot. lubot.ai"
    hashtags = data.get("hashtags") or []
    return CarouselResult(caption=caption, hook=hook, points=points, cta=cta, hashtags=hashtags)


def derive_card_headline(post_text: str, max_words: int = 12) -> str:
    """Fallback pull-quote for the insight card when the LLM didn't supply one:
    the post's first sentence, trimmed to a short headline."""
    first = re.split(r"(?<=[.!?])\s", post_text.strip())[0] if post_text.strip() else ""
    words = first.split()
    return " ".join(words[:max_words]).rstrip(".,;:—- ")


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
    """Create an AsyncOpenAI client for the NVIDIA NIM API (primary, free)."""
    return AsyncOpenAI(
        base_url=NVIDIA_BASE_URL,
        api_key=os.environ.get("NVIDIA_API_KEY", ""),
        max_retries=2,  # fail over to OpenRouter sooner than burning 5 retries on a 429
        timeout=REQUEST_TIMEOUT,
    )


def get_fallback_client() -> AsyncOpenAI | None:
    """Create an AsyncOpenAI client for OpenRouter (fallback), or None if unconfigured.

    Returns None when OPENROUTER_API_KEY is missing, so write_post just uses NIM.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return None
    return AsyncOpenAI(
        base_url=os.environ.get("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL),
        api_key=api_key,
        max_retries=1,
        timeout=REQUEST_TIMEOUT,
    )


async def _generate_once(
    client: AsyncOpenAI,
    model: str,
    system_prompt: str,
    user_prompt: str,
    topic_name: str,
) -> str | None:
    """Run one completion against a given provider/model. Returns raw text or None."""
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.8,
        max_tokens=MAX_OUTPUT_TOKENS,
    )

    # Report generation metadata to Langfuse (best effort)
    try:
        usage = response.usage
        get_client().update_current_generation(
            model=response.model or model,
            model_parameters={"temperature": 0.8, "max_tokens": MAX_OUTPUT_TOKENS},
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
    # Nemotron sometimes puts the answer in reasoning_content
    if not raw_text:
        raw_text = getattr(msg, "reasoning_content", None)
    return raw_text


@observe(as_type="generation")
async def write_post(
    topic_name: str,
    topic_description: str,
    articles: list[ScrapedArticle],
    performance_context: str | None = None,
    book_concepts: list[str] | None = None,
    podcast_context: str | None = None,
    recent_posts: list[str] | None = None,
) -> WriterResult | None:
    """Generate a LinkedIn post. Tries NVIDIA NIM first, falls back to OpenRouter.

    Returns WriterResult on success, None if every provider fails/returns empty.
    """
    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(
        topic_name=topic_name,
        topic_description=topic_description,
        articles=articles,
        performance_context=performance_context,
        book_concepts=book_concepts,
        podcast_context=podcast_context,
        recent_posts=recent_posts,
    )

    # Primary = free NIM; fallback = OpenRouter (only when a key is configured).
    providers: list[tuple[str, AsyncOpenAI, str]] = [("NIM", get_llm_client(), NVIDIA_MODEL)]
    fallback = get_fallback_client()
    if fallback is not None:
        providers.append(("OpenRouter", fallback, OPENROUTER_MODEL))

    for name, client, model in providers:
        try:
            raw_text = await _generate_once(client, model, system_prompt, user_prompt, topic_name)
        except Exception as e:
            logger.warning("LLM call via %s (%s) failed: %s", name, model, e)
            continue
        if raw_text:
            logger.info("LLM response via %s (%s): %d chars", name, model, len(raw_text))
            return parse_response(raw_text)
        logger.warning("LLM via %s (%s) returned empty content", name, model)

    logger.warning("All LLM providers failed or returned empty content")
    return None


@observe(as_type="generation")
async def write_carousel(
    topic_name: str,
    topic_description: str,
    articles: list[ScrapedArticle],
    performance_context: str | None = None,
    book_concepts: list[str] | None = None,
    podcast_context: str | None = None,
    recent_posts: list[str] | None = None,
) -> CarouselResult | None:
    """Generate a swipeable CAROUSEL (Phase 2.21) from a topic — hook, points, CTA + feed caption.
    Same provider fallback (NIM -> OpenRouter) and same material as write_post; only the prompt and
    parser differ. Returns CarouselResult, or None if every provider fails/returns an unusable deck."""
    system_prompt = build_carousel_system_prompt()
    user_prompt = build_carousel_user_prompt(
        topic_name=topic_name,
        topic_description=topic_description,
        articles=articles,
        performance_context=performance_context,
        book_concepts=book_concepts,
        podcast_context=podcast_context,
        recent_posts=recent_posts,
    )

    providers: list[tuple[str, AsyncOpenAI, str]] = [("NIM", get_llm_client(), NVIDIA_MODEL)]
    fallback = get_fallback_client()
    if fallback is not None:
        providers.append(("OpenRouter", fallback, OPENROUTER_MODEL))

    for name, client, model in providers:
        try:
            raw_text = await _generate_once(client, model, system_prompt, user_prompt, topic_name)
        except Exception as e:
            logger.warning("Carousel LLM via %s (%s) failed: %s", name, model, e)
            continue
        if raw_text:
            result = parse_carousel_response(raw_text)
            if result:
                logger.info("Carousel via %s (%s): %d points", name, model, len(result.points))
                return result
            logger.warning("Carousel LLM via %s (%s) returned an unparseable/empty deck", name, model)
        else:
            logger.warning("Carousel LLM via %s (%s) returned empty content", name, model)

    logger.warning("All LLM providers failed to produce a carousel")
    return None
