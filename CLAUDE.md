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
15c. Knowledge Base — books→chunks→vectors→RAG in writer (NEXT)
15d. Full E2E Validation — ESL voice cloning, post-processor, 4 bug fixes (IN PROGRESS)
### Phase 3: Frontend + Deploy
16. React Dashboard (publisher.lubobali.com — React/Vite/Tailwind/shadcn, mobile-first)
17. Deploy + First Real Post

## Current Status (Mar 23, 2026)
- **371 tests**, all green, lint clean, CI green (commit f6dc0dc)
- **Model**: nvidia/llama-3.1-nemotron-ultra-253b-v1 (with reasoning_content fallback)
- **136 RSS sources** across 7 categories (no Reddit)
- **Source priority ranking**: biohacker Dave Asprey > Saladino > Brecka, all categories respect YAML order
- **Post-processor**: strips dashes, apostrophes (ESL), JSON wrappers, filler phrases, news-anchor openings, enforces line breaks
- **ESL voice cloning**: 20 real posts analyzed, no apostrophes, casual grammar, writing patterns
- **Screenshots**: real article URLs only, error page detection (400-500), cookie removal, lubot.ai for My Agent
- **Week generation**: always Sun-Sat (all 7 categories guaranteed every week)
- **My Agent posts**: ONE feature per post, no ad copy, no feature dumps
- **Bugs fixed**: JSON wrappers ✅, apostrophes ✅, dashes ✅, news-anchor openings ✅ (0/7 bugs on last run)
- **Remaining**: posts still open with news facts instead of Lubo's reaction/insight (prompt tuning needed)
- **Remaining**: apostrophe stripping can break sentences at boundaries ("Whats Not a car guy")
- **Next session**: Fix insight-first openings, fix apostrophe edge cases, then Step 15c or 16
