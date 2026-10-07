<h2 align="center">LuBot Publisher</h2>

<p align="center"><strong>An AI content pipeline you can rebuild for your own topics, data and voice</strong></p>

<p align="center">
  <a href="docs/BUILD_YOUR_OWN.md"><strong>📘 Build your own</strong></a> &nbsp;|&nbsp;
  <a href="https://lubot.ai"><strong>LuBot.ai</strong></a> &nbsp;|&nbsp;
  <a href="https://www.linkedin.com/in/lubo-bali"><strong>LinkedIn</strong></a> &nbsp;|&nbsp;
  <a href="https://lubobali.com/"><strong>Lubo Bali</strong></a>
</p>

---

I built this for myself. Every week it reads podcasts, blogs, market data, my own git log and my coding stats, writes **9 posts in my voice**, designs the image, and waits for one tap on my phone. Then it publishes to **LinkedIn and X**. It has been live since June 2026.

**This repo is a working example, not a template to copy.** My topics, sources and writing style are mine. What's reusable is the **pattern**, and the guide shows how to rebuild it around your own topics, your own data and your own voice:

### 👉 [docs/BUILD_YOUR_OWN.md](docs/BUILD_YOUR_OWN.md): step-by-step guide

<table>
  <tr>
    <td width="50%" valign="top"><img src="docs/screenshots/06-dashboard-overview.png" alt="Approval dashboard: a Building in Public draft with its stat card and separate LinkedIn / X versions"></td>
    <td width="50%" valign="top"><img src="docs/screenshots/07-post-draft.png" alt="A Biohacker carousel draft with the X thread version below it"></td>
  </tr>
  <tr>
    <td align="center"><i>Approval dashboard: each draft has its own LinkedIn and X version</i></td>
    <td align="center"><i>A Biohacker carousel draft, 8 slides, with a 4-tweet X thread</i></td>
  </tr>
</table>

---

## The pattern

```
 TOPIC ─▶ REAL MATERIAL ─▶ GROUND ─▶ WRITE ─▶ CLEAN + TRUTH CHECK ─▶ IMAGE ─▶ YOU APPROVE ─▶ PUBLISH
```

| Stage | What happens | Where | Change it with |
|---|---|---|---|
| **Topic** | picks which topic owns today's slot | `topic_rotator.py` | `config/topics.yaml`, `config/schedule.yaml` |
| **Material** | RSS, podcasts (transcribed), APIs, **your own data** | `scraper.py`, `podcast_insights.py`, `*_insights.py` | `config/scraper_sources.yaml` + a small module for your own data |
| **Dedup** | skips URLs, titles and ideas already used | `duplicate_checker.py` | automatic |
| **Ground** | 2–3 passages from your books/notes as background | `knowledge_base.py` | `books/*.pdf` + `scripts/ingest_books.py` |
| **Write** | LLM writes in your voice from the material only | `writer.py` | `config/voice_rules.yaml`, `templates/voice_samples.txt` |
| **Clean + check** | strips model junk, enforces style, rejects made-up numbers | `post_processor.py` | toggle fixes in `process_post()` |
| **Image** | branded card, chart or stat card rendered by Playwright | `cards.py`, `screenshotter.py` | `INSIGHT_CARDS`, `static/assets`, `static/fonts` |
| **Approve** | drafts wait as PENDING on a mobile dashboard | `api.py`, `static/dashboard.html` | — |
| **Publish** | LinkedIn post + first-comment link, X thread + reply | `publisher.py`, `cron.py` | `.env` keys |

Adding an RSS-based topic takes **zero code**: one entry in `topics.yaml` and a list of feeds in `scraper_sources.yaml`. Adding your own private data source (training log, sales sheet, git log…) is **one small module and one `elif`**. The guide has a full example.

---

## Why posts don't sound like AI slop

1. **Real material first.** The model writes, it doesn't invent. Every post starts from a source: an episode, a chart, a commit, an article.
2. **Your own data.** My best topics come from data only I have: my git log and my coding hours. Nobody can copy that.
3. **Your voice from your real posts.** 20 of my posts are the style reference, plus explicit style rules.
4. **Truth rules, built from real failures.** No invented numbers, tools, setups or experiences. Opinions are framed as opinions. Market numbers are checked against the data, and chain-of-thought dumps are rejected. See [Keep it honest](docs/BUILD_YOUR_OWN.md#11-keep-it-honest).
5. **A human approves every post.** Nothing goes out without a tap.

---

## Quick start

```bash
git clone https://github.com/lubobali/Lubo_AI_Publisher.git
cd Lubo_AI_Publisher
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env               # NVIDIA_API_KEY is the only required key
docker compose up publisher-db -d

python3 -m pytest tests/ -q        # safe: uses a separate publisher_test DB
python3 scripts/test_pipeline.py   # generate a week of drafts (nothing is published)
uvicorn src.api:app --port 8100    # read them at http://localhost:8100
```

Then follow **[BUILD_YOUR_OWN.md](docs/BUILD_YOUR_OWN.md)** to swap in your topics, sources and voice.

---

## Example: my setup

This is how I configured it. Use it as a reference for what a finished setup looks like.

<details>
<summary><b>7 topics, 9 posts a week, and where each one gets its material</b></summary>

| Topic | Primary source | Fallback | Image |
|---|---|---|---|
| **Biohacker** (3x/week) | Podcast episodes, transcribed and distilled: *The Human Upgrade* (Dave Asprey), *Paul Saladino MD*, *The Ultimate Human* (Gary Brecka) | 18 RSS feeds | Insight card |
| **AI News** | Weekly *Moonshots with Peter Diamandis* episode, read for news | 27 RSS feeds (OpenAI, DeepMind, NVIDIA, Hugging Face…) | Headline card |
| **Tech Talk** | Same episode, read for ideas worth a senior opinion | 23 RSS feeds (Pragmatic Engineer, Latent Space, Martin Fowler…) | Insight card |
| **Market Pulse** | Real index data from yfinance. The chart symbols are picked from the week's market podcasts | — | ECharts market card |
| **Investing Principle** | Finance blogs (JL Collins, A Wealth of Common Sense, Of Dollars and Data, The New Frontier) | CNBC / MarketWatch RSS | Insight card |
| **My Agent Build** | My real git log: commits grouped by feature, real +/- lines | — | Git card |
| **Building in Public** | My real coding week from DevTrack-AI / WakaTime: hours, languages, AI usage, momentum | — | Stat card |

**Schedule (Chicago time):** Biohacker is pinned to Sun 10am, Wed 7am and Fri 3pm. The other 6 topics rotate through Sun evening, Mon–Thu afternoons and Sat midday, and shift by one slot each week. The exact minute is random inside each window.

</details>

<details>
<summary><b>Knowledge base: 11 books + 4 blogs (~4,600 chunks)</b></summary>

*Designing Data-Intensive Applications* · *Fundamentals of Data Engineering* · *Data Engineering Cookbook* · *Fundamentals of Metadata Management* · *The Architecture of Open Source Applications* Vol 1 + 2 · *Dive into Deep Learning* · *Foundations of Large Language Models* · *Speech and Language Processing* (3e) · *Machine Learning Yearning* · *Illustrated Guide to Python 3*. Plus the 4 finance blogs above.

Pipeline: pypdf → clean → ~400-word chunks (50 overlap, sentence-aware) → `llama-nemotron-embed-vl-1b-v2` (2048-dim) → Postgres → numpy cosine, min score 0.35. No vector DB needed at this size. The PDFs are not in the repo.

</details>

<details>
<summary><b>Production layout</b></summary>

```
publisher-web     uvicorn: dashboard + REST API (127.0.0.1:8100, behind nginx + Let's Encrypt)
publisher-worker  APScheduler: daily_planner 00:01 · publish_approved every 5 min · nightly_backup 04:00
publisher-db      PostgreSQL 16: posts, knowledge base, transcripts, short links (8 tables)
```

Docker Compose on a Hetzner box, Forgejo CI with a real Postgres service, mirrored here.

</details>

---

## Tech stack

| Layer | Tech |
|---|---|
| Writer LLM | NVIDIA Nemotron 3 Ultra 550B (NIM). OpenRouter is the fallback. Any OpenAI-compatible model works |
| Embeddings | `llama-nemotron-embed-vl-1b-v2` (knowledge base), `nv-embedqa-e5-v5` (dedup) |
| Podcasts | Deepgram nova-3 transcription, cached in Postgres |
| Data | RSS/Atom, HackerNews search, yfinance, git, WakaTime |
| Images | Playwright renders HTML/CSS cards, ECharts for charts. AI image fallback |
| App | Python 3.12, FastAPI, SQLAlchemy 2.0, PostgreSQL 16, APScheduler |
| Publishing | LinkedIn REST API, X API (tweepy), UTM short links |
| Observability | Langfuse traces + quality scores (optional) |
| Ops | Docker Compose, nginx, Backblaze B2 backups, Forgejo CI |

---

## Project structure

```
config/                    ← start here: topics, sources, schedule, voice
templates/voice_samples.txt  your real posts (style reference)
src/
  cron.py                  worker: planner, publisher loop, backups
  scheduler.py             the pipeline: Pipeline.generate_post() + publishing
  topic_rotator.py         weekly rotation
  scraper.py               RSS / HackerNews scraper + ranking
  podcast_insights.py      podcast feed → transcript → distilled bullets
  transcription.py         Deepgram client
  git_insights.py          example: your git log as a source
  wakatime_insights.py     example: your coding stats as a source
  devtrack_insights.py     example: a weekly report file as a source
  stock_insights.py        example: market data as a source
  duplicate_checker.py     URL / title / embedding dedup
  knowledge_base.py        PDF → chunks → embeddings → search
  self_learner.py          past performance → hints for the writer
  writer.py                prompts + LLM calls + X thread version
  post_processor.py        cleanup, style, validation, number check
  cards.py, screenshotter.py, image_generator.py   images
  publisher.py             platform interface (LinkedIn, X, add your own)
  linkedin_client.py, x_client.py, shortlinks.py
  api.py                   FastAPI + dashboard
  analytics_worker.py, backup.py, models.py, db.py, observability.py
scripts/                   ingest_books, ingest_stock_feeds, test_pipeline, linkedin_auth, make_carousel
static/                    dashboard.html, fonts, logo, vendored ECharts
deploy/                    example nginx vhost
tests/                     pytest: real Postgres, external APIs mocked
docs/BUILD_YOUR_OWN.md     the guide
```

---

## Testing approach

- **Mock the edges** (LinkedIn, X, NVIDIA, Deepgram, RSS), **keep the logic real** (rotation, dedup, DB, scoring), using a real Postgres in CI.
- Test the **pipeline**, not what the LLM says: prompt assembly, parsing, guardrails, structure.
- Tests are written first, code second. Every push runs lint plus the full suite.

---

[MIT License](LICENSE). Fork it, change everything, make it yours.

<p align="center">
  <i>Built by <a href="https://linkedin.com/in/lubo-bali">Lubo Bali</a> · <a href="https://lubot.ai">LuBot.ai</a></i>
</p>
