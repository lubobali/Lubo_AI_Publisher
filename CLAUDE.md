# LuBot Publisher — Project Instructions

## Overview
- **LuBot Publisher**: Autonomous LinkedIn Content Engine for Lubo Bali's personal profile
- **Fully separate from LuBot** — separate repo, separate DB, separate Docker container
- **Purpose**: Daily AI-written LinkedIn posts with real screenshots, zero manual work
- **LinkedIn Profile**: linkedin.com/in/lubo-bali (2,227 followers at project start)
- **Server**: Hetzner (same machine as LuBot, separate container)
- **Repo**: Forgejo + GitHub mirror

## Architecture
- **Language**: Python 3.12
- **AI Model**: NVIDIA Nemotron Ultra 253B (via NIM API)
- **Database**: PostgreSQL 16 (Docker, separate from LuBot)
- **Screenshots**: Playwright
- **Posting**: Official LinkedIn API (w_member_social scope)
- **LinkedIn Client ID**: 863iqo5u8ah0mt

## Project Structure
```
lubot-publisher/
├── config/
│   ├── topics.yaml              Topic templates + rotation rules
│   ├── voice_rules.yaml         Writing style rules (Lubo's voice)
│   ├── schedule.yaml            Posting windows + randomization
│   └── scraper_sources.yaml     News sources per topic category
├── src/
│   ├── scheduler.py             Daily cron — picks topic, time, runs pipeline
│   ├── scraper.py               Multi-source web scraper
│   ├── writer.py                NVIDIA 253B post writer
│   ├── screenshotter.py         Playwright screenshot engine
│   ├── linkedin_client.py       OAuth + post/image upload via LinkedIn API
│   ├── token_manager.py         Token expiry monitor + re-auth flow
│   ├── topic_rotator.py         Rotates topics weekly, prevents repeats
│   ├── analytics_worker.py      Fetches engagement metrics daily
│   ├── self_learner.py          Adjusts content based on performance data
│   ├── duplicate_checker.py     Embedding similarity + URL dedup
│   └── db.py                    PostgreSQL connection + migrations
├── templates/
│   └── voice_samples.txt        10-15 of Lubo's real LinkedIn posts
├── tests/                       Full test suite
├── .forgejo/workflows/test.yml  CI pipeline
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── pyproject.toml
```

## How to Build & Test
**Follow these rules automatically every session. No reminders needed.**

### Commands
- Run all tests: `python3 -m pytest tests/ -q`
- Run specific: `python3 -m pytest -q -k test_name`
- Lint: `ruff check .` | Format: `ruff format .`
- Pre-commit hooks auto-run ruff + pytest on every commit
- Forgejo CI runs full suite on every push

### RECR Loop — How to Write Code (Matt Harrison p.301)
1. **R**equirements: Write the TEST first that defines the behavior
2. **E**xecute: Implement ONE task to make that test pass
3. **C**heck: Run tests, verify green
4. **R**epeat: Next task
- Keep each task SHORT — one test at a time
- Only accept changes that move a test from red to green

### What to Mock vs What to Keep Real
- **MOCK**: External APIs (LinkedIn API, NVIDIA NIM, web scraping responses)
- **REAL**: Internal logic (rotation, scheduling, dedup, analytics calculations, DB operations)
- "Mock external boundaries, not internal business logic"

### CI Pipeline (Forgejo Actions) — `.forgejo/workflows/test.yml`
CI mirrors production. If CI passes, the code works in prod. No shortcuts.
1. **PostgreSQL 16 service container** — real DB, same as production
2. **Install deps** from `requirements.txt`
3. **Ruff lint + format**
4. **App startup check** — verify imports work
5. **ALL unit tests** — no ignores, no skips

### CI Rules — MUST FOLLOW
- **Any new pip dependency MUST be added to `requirements.txt`**
- **CI must pass before deploying** — no exceptions
- **Never skip tests or ignore directories in CI**
- **Never create a separate CI requirements file**

### Test Patterns
- **Parametrize**: `@pytest.mark.parametrize` for multiple inputs
- **Fixtures**: conftest.py for shared setup
- **Coverage**: Tool to find blind spots, NOT a target metric

### Testing LLM/AI Features
- Test the PIPELINE, not the LLM output (non-deterministic)
- Mock the LLM call, assert on structure not exact wording
- Test: context building, prompt assembly, response parsing — NOT what the LLM says

### AI Agent Failure Modes to Watch For
- Mirroring implementation in tests (testing HOW, not WHAT)
- Over-asserting details that should be flexible
- Introducing hidden nondeterminism
- Weakening tests to make them pass — RED FLAG, always reject

### Quality Rules
- ZERO test failures before moving forward — no exceptions
- ALL new code: write test FIRST, then implement
- Full plan: docs/LuBot_Publisher_Plan.txt

## LinkedIn API Details
- **Client ID**: 863iqo5u8ah0mt
- **OAuth scope**: w_member_social
- **Token TTL**: 2 months (5,184,000 seconds)
- **No refresh tokens** — re-auth required every 60 days
- **Redirect URLs**: https://lubot.ai/auth/linkedin/callback, http://localhost:8000/auth/linkedin/callback
- **Endpoints**: /rest/posts (CREATE, DELETE), /rest/images (initializeUpload), /rest/videos

## Build Order (17 Steps)
### Phase 1: Core Components (DONE)
1. Scaffold (repo, Docker, CI) ✅
2. Database (PostgreSQL, 4 tables, migrations) ✅
3. LinkedIn OAuth ✅
4. LinkedIn Post (text only) ✅
5. LinkedIn Image Upload + Post ✅
6. Web Scraper ✅
7. Playwright Screenshotter ✅
8. AI Writer (NVIDIA 253B) ✅
### Phase 2: Pipeline Backend (DONE)
9. Publisher Interface (multi-platform architecture) ✅
10. Duplicate Checker (URL dedup, title similarity, NVIDIA embeddings, category balance) ✅
11. Topic Rotator + Schedule Randomizer (7-topic weekly rotation, random posting times) ✅
12. Analytics Worker (fetch engagement metrics, recalculate topic performance) ✅
13. Self-Learning Engine (performance reports, trend detection) ✅
14. Daily Pipeline (scraper→dedup→writer→screenshot→pending, approval workflow) ✅
15. Backend API Routes (FastAPI REST for dashboard) ✅
### Phase 2.5: Content Quality (IN PROGRESS)
15a. Scraper Source Overhaul — 136 verified RSS feeds, no Reddit ✅
15b. Screenshot + Image Fixes — error detection, cookie removal, lubot.ai SPA, 1.5x scale ✅
15d. Full E2E Validation — ESL voice cloning, post-processor, 4 bug fixes (IN PROGRESS)
### Phase 2.6: Langfuse Observability (DONE)
15e. Langfuse integration — 7 pipeline stages traced, 5 quality scores, prompt versioning ✅
### Phase 2.7: Git Insights — Real Work Posts (DONE)
15f. Git Insights module — src/git_insights.py, SSH to staging, parse git log, filter noise, group by feature ✅
15g. Topic rotation update — DE Work → My Agent Build (2x My Agent per week via rotation) ✅
15h. Staging screenshots — screenshot staging.lubot.ai for my_agent + my_agent_git ✅
15i. Writer context — git commits fed to writer, anti-hallucination rules, build log prompts ✅
### Phase 2.75: WakaTime Insights — Building-in-Public Stats (before RAG)
15m. WakaTime Insights module — SSH read daily archives, parse, return ScrapedArticle (mirror git_insights.py) ✅
15n. Weekly aggregation + metrics — WeeklyStats dataclass, week-over-week momentum delta, include_costs toggle ✅
15o. Topic/rotation wiring — Building in Public slot (replaced Big Tech), scheduler dispatch, enrich my_agent_git, writer block ✅
15p. Stat-card screenshot — take_wakatime_screenshot() renders a building-in-public PNG (hours, language bars, projects, AI stats, cost, momentum badge); wired into scheduler ✅ — PHASE 2.75 COMPLETE
### Phase 2.8: Knowledge Base + RAG (plan finalized Jun 15 — full detail in docs/LuBot_Publisher_Plan.txt)
Model: nvidia/llama-nemotron-embed-vl-1b-v2 (2048-dim, 8192-tok, VERIFIED vs NVIDIA docs; same as lubot staging PDF RAG v2). Flat ~400-word chunks. JSON embeddings + numpy cosine (no pgvector/FAISS). Grounding ONLY tech_talk/my_agent_git/ai_news. NEVER name the book in posts. Keep raw PDFs.
15c-1. PDF extraction (src/knowledge_base.py, pypdf) — text per book, skip empty pages, drop page-numbers + running headers/footers (incl. page-numbered footers via digit-normalized repeat detection) ✅
15c-2. Chunker — ~400-word chunks, 50 overlap, sentence-boundary aware (pure, tested)
15c-3. Embedding client — NVIDIA NIM POST, modality=text, passage/query, L2-normalized
15c-4. DB model + migration — publisher_knowledge_base table, idempotent store
15c-5. Retrieval — cached numpy matrix, cosine top-k, min-score threshold
15c-6. Writer wiring — inject 2-3 concepts for the 3 techie categories, never cite book
15c-7. Ingest script — run on 11 books, verify manually, keep PDFs
15c-8. Langfuse trace + E2E eyeball
### Phase 2.9: Post Quality Tuning
15j. Prompt tuning with Langfuse data — compare prompt_version scores, target compliance > 0.9
15k. Fix known issues — reaction vs facts, apostrophe boundary bug, category-specific rules, Cloudflare captcha detection in screenshotter, screenshot_url leak in plain-text parser, hashtag duplication in plain-text fallback
15l. Voice quality gate — 7-day test, human approval scoring, final pass
### Phase 3: Frontend + Deploy
16. React Dashboard (publisher.lubobali.com — React/Vite/Tailwind/shadcn, mobile-first)
17. Deploy + First Real Post

## Current Status (Jun 15, 2026)
- **509 tests**, all green, lint clean
- **Phase 2.8 RAG STARTED** — 15c-1 done: src/knowledge_base.py extracts clean text from book PDFs (pypdf mocked in tests; verified on real DDIA + ML Yearning, footers stripped). pypdf==6.13.2 in requirements. Next: 15c-2 chunker.
- **WakaTime Insights (Phase 2.75) COMPLETE** — src/wakatime_insights.py: SSH read 2 weeks of daily archives → WeeklyStats → ScrapedArticle. Verified on real data (58h/wk, Python 55%, LuBot 99%, AI tokens/cost, "up 345% vs last week" momentum). include_costs toggle (costs posted publicly). 15m–15p ALL DONE.
- **Stat-card screenshot (15p)**: take_wakatime_screenshot() + pure _build_wakatime_html() render a polished dark LinkedIn-ready PNG (big hours number, gradient language/project bars, AI sessions/prompts/tokens/cost, directional momentum badge). build_screenshot_fields() adapts WeeklyStats→kwargs. Wired into scheduler; verified by rendering a real PNG from live archives.
- **Rotation change (Option A)**: "Big Tech" slot REPLACED by "Building in Public" (sources_key: wakatime). Scheduler dispatches wakatime → WakaTimeInsights (like my_agent_git → GitInsights). my_agent_git posts ENRICHED with WakaTime stats (passed as 2nd article to writer). Writer has building_in_public prompt block (exact numbers, anti-hallucination). wakatime image style added; dashboard URL never screenshotted (login-walled) → generated-image fallback until 15p.
- **Test note**: added autouse _default_topic fixture in test_scheduler.py + test_observability.py — pins get_todays_topic to ai_news so pipeline-flow tests don't depend on which category the rotation lands on (removed pre-existing hidden coupling).
- **Still TODO Phase 2.75**: stat-card screenshot (15p)
- **Git Insights LIVE** — SSH to staging, parse commits, feed real work to writer
- **Langfuse observability LIVE** — 7 pipeline stages traced, 5 quality scores, prompt versioning
- **Model**: nvidia/llama-3.1-nemotron-ultra-253b-v1 (with reasoning_content fallback)
- **136 RSS sources** across 7 categories (no Reddit)
- **Source priority ranking**: biohacker Dave Asprey > Saladino > Brecka, all categories respect YAML order
- **Post-processor**: strips dashes, apostrophes (ESL), JSON wrappers, filler phrases, news-anchor openings, enforces line breaks + compliance scoring
- **ESL voice cloning**: 20 real posts analyzed, no apostrophes, casual grammar, writing patterns
- **Screenshots**: real article URLs, error page detection, cookie removal. my_agent→staging.lubot.ai, my_agent_git→terminal git screenshot
- **Week generation**: always Sun-Sat (all 7 categories guaranteed every week)
- **My Agent posts**: ONE feature per post, no ad copy, no feature dumps
- **Bugs fixed**: JSON wrappers ✅, apostrophes ✅, dashes ✅, news-anchor openings ✅ (0/7 bugs on last run)
- **Pipeline post-processing**: process_post() + validate_post() now run inside Pipeline.generate_post() for full Langfuse tracing
- **Remaining**: posts still open with news facts instead of Lubo's reaction/insight (prompt tuning needed)
- **Remaining**: apostrophe stripping can break sentences at boundaries ("Whats Not a car guy")
- **Git Insights**: src/git_insights.py — SSHs to staging, parses git log, filters noise, groups by feature, returns ScrapedArticle
- **My Agent Build**: replaced DE Work — 2x My Agent per week (one demo-style, one build-log from git)
- **Staging screenshots**: my_agent→staging.lubot.ai, my_agent_git→terminal-style git screenshot (take_git_screenshot)
- **Post-processor**: ensure_paragraph_spacing() adds blank lines between dense text blocks
- **SSH host**: 178.156.214.8 (public IP, not Tailscale — Docker compatible)
- **E2E test**: 7/7 posts generated successfully (Mar 24). 3 bugs logged for Phase 2.9
- **11 books downloaded** for Phase 2.8 RAG: DDIA, Fundamentals of DE, Metadata Mgmt, Python Guide, Effective Testing, ML Yearning, Dive into DL, SLP3 (Jurafsky), LLM Foundations, DE Cookbook, AOSA Vol 1+2
- **Books location**: `/Users/lu/Library/Mobile Documents/com~apple~CloudDocs/Data/` (Mac). Copy to server `/srv/lubot-publisher/books/` before processing
- **Next session**: Phase 2.8 (Knowledge Base/RAG — PDF extract → chunk → embed → PostgreSQL → wire into writer)

## Phase 2.6: Langfuse Observability — DONE (Mar 23, 2026)
**Full plan**: `/Users/lu/spr_full_stack_AI/langfuse_integration_plan.txt`
**Purpose**: Enterprise-grade AI observability — trace every LLM call, score quality, measure prompt impact.
**Result**: 47 new tests (371→418), all green. Live on us.cloud.langfuse.com. 7/7 pipeline test posts generated with full tracing.

### Langfuse Credentials (saved in .env)
- Project: "Lubo Publisher" on us.cloud.langfuse.com
- Keys in `.env`: LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY, LANGFUSE_HOST

### What to Instrument (7 layers) — ALL DONE
1. `scheduler.py` → `Pipeline.generate_post()` — root trace ✅
2. `scraper.py` → `scrape_topic()` — span ✅
3. `duplicate_checker.py` → `check_article()` + `get_embedding()` — span + generation ✅
4. `writer.py` → `write_post()` — generation (MOST IMPORTANT) ✅
5. `post_processor.py` → `process_post()` — span + compliance scoring ✅
6. `screenshotter.py` → `take_screenshot()` — span ✅
7. `api.py` → `approve_post()`/`reject_post()` — human approval score ✅

### 5 Quality Scores — ALL DONE
1. **llm_compliance** (0-1): how many post-processor fixes were needed ✅
2. **validation** (0/1): did validate_post() pass ✅
3. **parse_quality** (0-1): how clean was LLM JSON output ✅
4. **source_quality** (0-1): usable articles / total scraped ✅
5. **human_approval** (0/1): Lubo approved or rejected ✅

### DB Change ✅
- `langfuse_trace_id = Column(String(100), nullable=True)` on PublisherPost

### New Files ✅
- `src/observability.py` — Langfuse init, re-exports @observe
- `scripts/test_pipeline.py` — 7-day pipeline runner with full Langfuse tracing

### Implementation Order (9 steps, RECR style — test first)
1. Setup (pip install, observability.py, env vars, DB column) ✅
2. Root trace + Writer generation ✅
3. Post-processor scoring ✅
4. Embedding + Dedup tracing ✅
5. Scraper + Screenshot tracing ✅
6. Human approval scoring ✅
7. Prompt versioning (hash system prompt) ✅
8. Validation + Parse scoring ✅
9. Dashboard screenshot + LinkedIn post — **manual step (Lubo)**

### Mock Rules for Langfuse Tests
- **MOCK**: Langfuse API calls (don't send real traces in tests)
- **REAL**: Score calculations, fix counting, prompt hashing
