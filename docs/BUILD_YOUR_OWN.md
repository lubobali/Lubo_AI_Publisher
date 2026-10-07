# Build Your Own Content Pipeline

This repo is **my** pipeline: my topics, my sources, my voice, my data. Don't copy it as it is. The posts would sound like me, and nobody needs two of me on LinkedIn.

What you *can* take is the **pattern**. It's general:

```
pick a topic  →  get REAL material  →  ground it  →  write in YOUR voice
     →  clean + check the truth  →  make an image  →  you approve  →  publish
```

This guide walks through every part, says which file controls it, and gives examples for building something completely different (a fitness coach, a real-estate agent, a game dev, a researcher…).

> Throughout the guide, **"no code"** means you only edit YAML or text files. **"Small code"** means one short Python change, with an example.

---

## Contents

0. [Before you start: decide 3 things](#0-before-you-start-decide-3-things)
1. [Run it locally](#1-run-it-locally)
2. [Define your topics](#2-define-your-topics-no-code)
3. [Add your sources](#3-add-your-sources-no-code)
4. [Set your schedule](#4-set-your-schedule-no-code--one-line)
5. [Teach it your voice](#5-teach-it-your-voice)
6. [Give it a knowledge base (optional)](#6-give-it-a-knowledge-base-optional)
7. [Plug in your own data (the real superpower)](#7-plug-in-your-own-data-small-code)
8. [Images](#8-images)
9. [Approval + publishing](#9-approval--publishing)
10. [Deploy](#10-deploy)
11. [Keep it honest](#11-keep-it-honest)
12. [Checklist: everything that is specific to me](#12-checklist-everything-that-is-specific-to-me)

---

## 0. Before you start: decide 3 things

Answer these on paper first. They drive every config file.

| Question | My answers | Example: fitness coach |
|---|---|---|
| **What topics, how often?** | 7 topics, 9 posts a week, biohacking 3x | 4 topics: training, nutrition, client wins, myth-busting. 5 posts a week |
| **Where does REAL material come from?** | podcasts, RSS, market data, my git log, my coding stats | RSS from sports-science journals, a podcast, their own client check-in spreadsheet |
| **What does my voice sound like?** | ESL, casual, short lines, no apostrophes | high energy, second person ("you"), one emoji max |

The most important rule in this project: **every post starts from real material.** The LLM writes. It does not invent the substance. Section 7 shows how to feed in data only you have, which is what makes your posts impossible to copy.

---

## 1. Run it locally

```bash
git clone https://github.com/lubobali/Lubo_AI_Publisher.git my-publisher
cd my-publisher
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env            # put your NVIDIA_API_KEY in (free at build.nvidia.com)
docker compose up publisher-db -d

python3 -m pytest tests/ -q     # uses a separate publisher_test DB, safe to run
```

Generate one week of drafts without publishing anything:

```bash
python3 scripts/test_pipeline.py
```

The drafts are saved as `pending` in Postgres. Start the dashboard to read them:

```bash
uvicorn src.api:app --port 8100     # open http://localhost:8100
```

> Any OpenAI-compatible LLM endpoint works. The writer uses the `openai` client against NVIDIA NIM by default. To use OpenRouter as the main provider or as the fallback, set `OPENROUTER_API_KEY` and `OPENROUTER_MODEL`. To pick another NIM model, change `NVIDIA_LLM_MODEL`.

---

## 2. Define your topics (no code)

📄 **`config/topics.yaml`**

```yaml
categories:
  - name: Training Tips                 # shown on the dashboard + sent to the writer
    description: One practical strength-training idea, coach's opinion, no bro-science
    sources_key: training               # links to sources + rules (snake_case)

  - name: Nutrition
    description: Evidence-based nutrition, simple and practical
    sources_key: nutrition

rotation:
  shift_per_week: 1
  cycle_length: 7
```

- `description` goes **straight into the prompt**. Write it like a brief to a ghostwriter: what the post is, what it isn't.
- `sources_key` is the ID that connects the topic to its sources (step 3), its schedule (step 4), its extra writing rules (step 5) and its image style (step 8).
- **Any new `sources_key` works with zero code.** If the key is not one of my special ones (`biohacker`, `ai_news`, `tech_talk`, `market_pulse`, `wakatime`, `my_agent_git`), it goes down the generic path: *scrape RSS → dedup → write → image*. That's enough for most people.

---

## 3. Add your sources (no code)

📄 **`config/scraper_sources.yaml`**: one list per `sources_key`.

```yaml
training:
  - name: Stronger by Science          # FIRST = highest priority
    type: rss
    url: https://www.strongerbyscience.com/feed/
  - name: T-Nation
    type: rss
    url: https://www.t-nation.com/feed/
  - name: HackerNews Fitness           # HN search, works for any keyword
    type: hackernews
    url: https://hn.algolia.com/api/v1/search_by_date
    search_query: strength training
```

- **Order matters.** Articles are ranked by source position first, then by how recent they are. Put your most trusted sources at the top.
- Supported types: `rss` (RSS/Atom) and `hackernews` (Algolia search).
- **Test every feed** before adding it. Plenty of sites have dead or blocked feeds. Reddit blocks headless fetching, so I don't use it.
- **Dedup is automatic.** The same URL, a very similar title, or an embedding over 0.85 cosine similarity to an earlier article is skipped (`src/duplicate_checker.py`).

### Podcasts as a source

Podcasts give much better material than headlines. One 2-hour episode carries more real ideas than 20 news blurbs. The audio is transcribed with Deepgram (`DEEPGRAM_API_KEY`), cached in Postgres, and distilled into bullets before the writer sees it.

```yaml
podcasts:
  training:
    - name: Some Strength Podcast
      url: https://feeds.example.com/podcast.xml    # the RSS feed with <enclosure> mp3s
```

⚠️ Podcasts are wired per topic in code. Today the scheduler only reads them for `biohacker`, `ai_news`, `tech_talk` and `market_pulse`. To make your topic podcast-first, copy the `elif category in ("ai_news", "tech_talk"):` branch in `Pipeline.generate_post()` (`src/scheduler.py`) and add your key. That's about 10 lines and the fallback to RSS comes with it.

---

## 4. Set your schedule (no code + one line)

📄 **`config/schedule.yaml`**

```yaml
timezone: America/New_York

posting_windows:
  weekday: {start_hour: 7,  end_hour: 9}     # random minute inside the window
  weekend: {start_hour: 10, end_hour: 12}

weekly_plan:            # Sunday → Saturday, each day lists its posts in time order
  sun: []
  mon: [{topic: rotate, window: weekday}]
  tue: [{topic: rotate, window: weekday}]
  wed: [{topic: rotate, window: weekday}]
  thu: [{topic: rotate, window: weekday}]
  fri: [{topic: rotate, window: weekday}]
  sat: []

rules:
  min_hours_between_same_day: 8
```

- `rotate` = the next topic in the weekly rotation. The order shifts by one slot every week, so Monday isn't always the same topic.
- A **pinned** topic always goes in the same slot. Mine is `biohacker` (3x a week). The pinned key is hardcoded as `BIOHACKER_KEY` in `src/topic_rotator.py`. **Change that one line to your own pinned topic's `sources_key`.** The rotator expects one topic with that key to exist, so even if you don't pin anything, keep one topic with that key in `topics.yaml`.
- Times are when **drafts** get made. Nothing is posted until you approve it.

---

## 5. Teach it your voice

This is what separates a good pipeline from AI slop. Three places:

### a) Your real posts (no code)
📄 **`templates/voice_samples.txt`**: paste **10–20 of your own real posts**, the ones that did well and that sound most like you. The model copies rhythm, line length and openings from these. This matters more than any rule.

### b) Your style rules (no code)
📄 **`config/voice_rules.yaml`**

| Section | What goes in it |
|---|---|
| `core_voice` | who you are and how you sound, in 3–6 lines |
| `esl_grammar` | my ESL quirks. **Delete or replace with your own grammar habits** |
| `writing_patterns` | how your posts open, flow, end |
| `structure` | hook lines, min/max length, how to end |
| `do` / `do_not` | hard rules |
| `hashtag_rules` | how many, which kind |
| `topic_specific` | extra rules **per topic**, keyed by the topic **name** in snake_case (`Training Tips` → `training_tips`) |

```yaml
topic_specific:
  training_tips:
    - Always give ONE thing the reader can do in their next session
    - Never give medical advice; say "talk to a physio" for injuries
```

### c) The persona line (small code)
📄 **`src/writer.py` → `build_system_prompt()`**: the first lines say *"You are writing LinkedIn posts as Lubo Bali…"*. Replace them with your name and who you are. A few topic prompt blocks in `write_post()` mention me by name too. Search for `Lubo` and rewrite those lines.

### d) The post-processor (small code)
📄 **`src/post_processor.py` → `process_post()`** runs after the LLM. Some fixes are **my** style, so remove the ones that aren't yours:

| Function | Keep? |
|---|---|
| `strip_model_meta`, markdown stripping, reasoning-dump guard | ✅ everyone (models leak junk) |
| `strip_filler_phrases`, `strip_news_anchor_openings` | ✅ most people |
| `strip_apostrophes` | ❌ **remove unless you also write without apostrophes** (it turns "don't" into "dont") |
| `strip_dashes` | your call |
| `normalize_brand` | change it to your own brand names |

---

## 6. Give it a knowledge base (optional)

Retrieval makes posts sound like someone who actually knows the field. The writer gets 2–3 relevant passages as **background**. It's told to never name the source and never pretend it did something it didn't.

```bash
mkdir -p books && cp ~/my-pdfs/*.pdf books/     # books/ is gitignored
python3 scripts/ingest_books.py                 # extract → chunk → embed → store
```

- PDF text is cleaned (page numbers, headers/footers), split into ~400-word chunks with overlap, embedded with `nvidia/llama-nemotron-embed-vl-1b-v2`, and stored in Postgres.
- Search is plain numpy cosine with a minimum score (0.35). If nothing is relevant, nothing is injected, which beats forcing it.
- **Turn it on for your topic:** add your `sources_key` to `GROUNDED_CATEGORIES` in `src/scheduler.py`.
- Blogs work too: `scripts/ingest_stock_feeds.py` ingests RSS posts into the same table. Copy it and swap in your feeds.

What to put in it: textbooks of your field, your own long-form writing, course notes, papers. Anything you would want a ghostwriter to have read.

---

## 7. Plug in your own data (small code)

This is the part that makes the pipeline **yours**. Nobody else can post from your git log, your gym logbook, your sales numbers or your garden sensor. My two strongest topics come from my own data:

- **My Agent Build** reads my real git log (`src/git_insights.py`)
- **Building in Public** reads my real coding stats from WakaTime/DevTrack (`src/wakatime_insights.py`, `src/devtrack_insights.py`)

Every source, whatever it is, ends up as the same small object:

```python
# src/scraper.py
@dataclass
class ScrapedArticle:
    title: str          # one-line headline of what happened
    url: str            # link (can be "" for private data)
    summary: str        # THE MATERIAL: everything the writer may use, with exact numbers
    source: str         # e.g. "My training log"
    published_at: datetime | None
```

### Recipe: a new data source in 3 steps

**1. Write a module that turns your data into a `ScrapedArticle`.** Keep the parsing pure (easy to test) and the file/API read at the edge.

```python
# src/training_log.py
import csv
from datetime import UTC, datetime, timedelta
from src.scraper import ScrapedArticle

def parse_week(rows: list[dict]) -> str:                 # pure → unit-test this
    total = sum(float(r["kg"]) * int(r["reps"]) for r in rows)
    prs = [r["lift"] for r in rows if r.get("pr") == "yes"]
    return f"Volume this week: {total:,.0f} kg. New PRs: {', '.join(prs) or 'none'}."

class TrainingLog:
    def __init__(self, path: str = "/data/training_log.csv"):
        self.path = path

    def get_weekly_article(self) -> ScrapedArticle | None:
        week_ago = datetime.now(UTC) - timedelta(days=7)
        with open(self.path) as f:                       # the only I/O
            rows = [r for r in csv.DictReader(f)
                    if datetime.fromisoformat(r["date"]).replace(tzinfo=UTC) >= week_ago]
        if not rows:
            return None
        return ScrapedArticle(
            title="My training week", url="", summary=parse_week(rows),
            source="My training log", published_at=datetime.now(UTC),
        )
```

**2. Add a topic** in `topics.yaml` with `sources_key: training_log`.

**3. Add one branch** in `Pipeline.generate_post()` (`src/scheduler.py`), next to the `my_agent_git` / `wakatime` branches:

```python
elif category == "training_log":
    selected_article = TrainingLog().get_weekly_article()
    if selected_article is None:
        return PipelineResult(success=False, error="No training logged this week")
```

That's it. Dedup, RAG, writer, post-processor, image, X thread, approval and publishing all work from there.

**Tip:** put **exact numbers in `summary`** and add a `topic_specific` rule like *"use only numbers from the material, exactly as written"*. For number-heavy topics, also run `numbers_grounded(post_text, summary)` (see Market Pulse in `scheduler.py`). It flags any number in the post that isn't in your data.

Ideas: Strava/Garmin export, Notion database, Google Sheet (CSV export), GitHub API, Shopify sales, a weather station, your newsletter archive, Readwise highlights.

---

## 8. Images

Every post gets one image. The pipeline tries these in order:

1. **A branded card for the topic** (`Pipeline._take_topic_card()` in `src/scheduler.py`)
2. **A screenshot of the source article** (generic topics with a URL)
3. **An AI-generated image** (`src/image_generator.py`)

The easiest upgrade is a **quote card** for your topic: add one line to `INSIGHT_CARDS` in `src/scheduler.py`:

```python
INSIGHT_CARDS = {
    "training": ("Training Tips", "Coach's notes, not medical advice"),   # (kicker, footer)
}
```

The card shows the post's headline in a designed frame (HTML/CSS rendered by Playwright). To make it yours:

- **Logo:** `static/assets/` (referenced from `src/screenshotter.py` / `src/cards.py`)
- **Fonts:** `static/fonts/` (woff2)
- **Colors, layouts, charts:** `src/cards.py` builds each card as a plain HTML string, and charts use the vendored ECharts. Copy a builder and change it.

I stopped using raw screenshots of other people's sites. A branded card looks better in the feed and doesn't send attention to someone else's page.

---

## 9. Approval + publishing

```
draft saved as PENDING → you open the dashboard on your phone
  → each platform version has its own Post / Reject button
  → the worker publishes approved posts every 5 minutes
```

I strongly recommend keeping the approval step. One bad AI post costs more than 100 good ones earn you.

**LinkedIn** (`LINKEDIN_*` in `.env`)
1. Create an app at linkedin.com/developers and add the products *Share on LinkedIn* + *Sign In with LinkedIn using OpenID Connect*.
2. Get a token (OAuth 2.0 tools → Create token, scopes `openid profile email w_member_social`), put it in `.env`, then run `python3 scripts/linkedin_auth.py urn` to fill in your person URN.
3. The token lasts **60 days** and there's no refresh token. Put a reminder in your calendar.
4. LinkedIn retires API versions about once a year. If posting fails with **426**, bump `LINKEDIN_API_VERSION`. A **401** means the token expired.

**X** (`X_*` in `.env`): OAuth 1.0a user keys from the X developer portal. The pipeline writes a **separate short thread** for X instead of reposting the LinkedIn text.

**Links:** links go in the **first comment** (LinkedIn) or a self-reply (X), not the post body. Each link is a short `/go/{code}` URL that redirects with UTM tags, so you can see which platform sent traffic (`src/shortlinks.py`). Point it at your own site.

**Another platform?** Subclass `Publisher` in `src/publisher.py` (`publish_text`, `publish_image`, `get_post_url`) and register it in `get_publisher()`.

---

## 10. Deploy

`docker-compose.yml` runs three containers:

| Service | Does |
|---|---|
| `publisher-web` | FastAPI dashboard + API |
| `publisher-worker` | `src/cron.py`: plans the day at 00:01, publishes approved posts every 5 min, backs up at 04:00 |
| `publisher-db` | Postgres 16 with a persistent volume |

```bash
docker compose up -d --build
```

- `deploy/publisher.lubot.ai.conf` is my nginx vhost. Use it as a template (reverse proxy to `127.0.0.1:8100` + Let's Encrypt).
- **Protect the dashboard.** Anyone who can open it can publish as you. Put it behind auth (basic auth, Cloudflare Access, Tailscale…).
- My worker mounts another repo read-only (`/srv/lubot-staging`) for the git/WakaTime topics. **Remove that volume** or point it at your own data.
- Backups to Backblaze B2 switch on only if `B2_*` is set. Without it they're a no-op, so set up *some* backup. Your knowledge base takes hours to rebuild.
- Never run the test suite against the production database. Tests use `publisher_test`, and `conftest.py` redirects any database whose name doesn't end in `_test`.

---

## 11. Keep it honest

An AI ghostwriter's worst failure isn't bad grammar. It's **confident lies under your name.** These guardrails came from real bugs I hit, so keep them:

| Problem I hit | Guardrail |
|---|---|
| Invented "I built a bitcoin pipeline this week" from a podcast | Opinion topics are framed as opinion. "I did" only comes from real data (step 7) |
| Wrong numbers about my own product | `my_agent_features` list in `voice_rules.yaml` + "exact numbers only" rule |
| Stats the model "remembered" from training | Truth rule: if a number isn't in the material, say it in words |
| "I just listened to this episode" (I hadn't) | Never claim you consumed the source. Cite it instead |
| Fake market percentages | `numbers_grounded()` checks every number against the data |
| The model's chain-of-thought got published | Plain-text fallback rejects reasoning dumps and fails closed |
| Same idea posted twice | Post embeddings + the last 3 posts per topic go to the writer |

Write your own rules into the `do_not` list in `voice_rules.yaml`. And **read every draft before you approve it**. That's what the dashboard is for.

---

## 12. Checklist: everything that is specific to me

Change or delete these when you make it yours:

- [ ] `config/topics.yaml`: my 7 topics
- [ ] `config/scraper_sources.yaml`: my feeds and podcasts
- [ ] `config/schedule.yaml`: my Chicago-time plan
- [ ] `config/voice_rules.yaml`: my ESL voice, `topic_specific`, `my_agent_features` (facts about my product LuBot)
- [ ] `templates/voice_samples.txt`: my real posts
- [ ] `src/writer.py`: persona text + topic blocks that name me
- [ ] `src/post_processor.py`: `strip_apostrophes`, `normalize_brand`
- [ ] `src/topic_rotator.py`: `BIOHACKER_KEY` (pinned topic)
- [ ] `src/scheduler.py`: special branches (`my_agent_git`, `wakatime`, `market_pulse`…), `GROUNDED_CATEGORIES`, `INSIGHT_CARDS`
- [ ] Links + CTAs that point to lubot.ai: `src/shortlinks.py`, `src/scheduler.py`, carousel CTA in `src/screenshotter.py`
- [ ] `static/assets/lubot-logo.png` and the "LUBO BALI · lubot.ai" signature on cards (`src/cards.py`, `src/screenshotter.py`)
- [ ] `docker-compose.yml`: the `/srv/lubot-staging` mount
- [ ] `deploy/`: my domain
- [ ] `tests/`: some tests pin my config (topic names, schedule). Update them along with your config, but **don't delete the pipeline tests**

When the suite is green and one week of drafts reads like *you* on a good day, you're done.
