# WINK — What I Need to Know

[![Tests](https://github.com/lauratrevino/WINK-temp/actions/workflows/tests.yml/badge.svg)](https://github.com/lauratrevino/WINK-temp/actions/workflows/tests.yml)

WINK is an AI-powered academic support platform for college students. Students upload their own course materials (syllabi, calendars, assignment instructions, notes) and WINK answers questions, tracks deadlines, generates practice questions, and helps with grade calculations — all grounded in what they've actually uploaded, not generic knowledge.

WINK was created by Dr. Laura L. Trevino at the University of Texas at El Paso as part of a research pilot studying how AI can support student success during the first year of college.

## What it does

- **Chat** — Ask questions about course material; the AI names the specific uploaded document it drew from (shown in the UI as "mentioned by name — not an independently verified citation," since it's the model naming a source, not a verified link to an exact passage); conversations are saved, exportable, and shareable
- **Deadlines & Calendar** — Automatically extracts deadlines from uploaded documents, with a full calendar view, conflict detection, and reminder emails
- **Practice & Assessment** — Generates study materials, flashcards, and quizzes from course material; an Assessment Quiz mode checks current knowledge and builds a personalized study plan
- **Grade Calculator** — Pulls grading breakdowns from an uploaded syllabus and calculates what's needed on remaining work
- **Progress & Wrapped** — Personal engagement stats and a semester-in-review page
- **Admin tools** — Analytics dashboard, per-student research view, a live health/diagnostics page, and a research-data export pipeline (raw + faculty-rated) for studying answer accuracy

## Tech stack

- **Backend:** Flask 3, PostgreSQL (via `psycopg2`, connection-pooled)
- **AI:** Anthropic Claude for chat/generation; Voyage AI for semantic document search (optional — falls back to TF-IDF via scikit-learn if not configured)
- **Document parsing:** `pypdf`, `python-docx`, `python-pptx`, `openpyxl`, `pytesseract` (OCR)
- **Email:** Amazon SES via SMTP, with SNS-based bounce/complaint handling
- **Frontend:** Server-rendered Jinja2 templates, vanilla JS (no framework/build step)
- **Security:** Hash-based Content-Security-Policy (no `unsafe-inline`), CSRF protection, DB-backed rate limiting, hashed password-reset tokens
- **Tests:** `pytest`, run against a real PostgreSQL database (not mocked) — 111 tests covering registration, document upload/parsing, chat, retrieval ranking, spaced repetition, concurrency, CSP correctness, resource-bounding/performance regressions, and a starting AI-quality golden dataset

## Project structure

```
wink/
├── blueprints/        # Route handlers, one file per feature area (auth, chat, documents, ...)
├── services/          # Business logic — document parsing, retrieval, grading, research tracking, etc.
├── config.py           # All configuration, read from environment variables
├── extensions.py       # DB connection pool, CSRF, Anthropic/Voyage clients
├── security.py         # Auth decorators, rate limiting
└── errors.py            # Centralized error logging

templates/              # Jinja2 templates, one per page
static/                 # CSS, JS, images
tests/                  # pytest suite (runs against a real Postgres DB)
Dockerfile              # Production entrypoint (gunicorn, configurable worker count)
```

## Running locally

**Requirements:** Python 3.11+, PostgreSQL, and (optionally) `tesseract-ocr` installed on the system for image OCR.

```bash
pip install -r requirements.txt

export DATABASE_URL="postgres://user:pass@localhost/wink_dev"
export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export ADMIN_EMAIL="you@example.com"
export ANTHROPIC_API_KEY="sk-..."

python3 app.py
```

The database schema is created automatically on first run (see `wink/extensions.py:init_db()` — every table is `CREATE TABLE IF NOT EXISTS`, so it's safe to run repeatedly).

### Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SECRET_KEY` | Yes | Flask session/CSRF signing key — must be fixed in production (see the startup error if unset for why) |
| `ADMIN_EMAIL` | Yes | Which account gets admin access |
| `ANTHROPIC_API_KEY` | Yes (for chat) | Chat/generation model |
| `VOYAGE_API_KEY` | No | Enables semantic document search; falls back to TF-IDF if unset |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASS` | No | Outbound email (verification, reminders); logs to console instead if unset |
| `CRON_SECRET` | No | Authorizes scheduled jobs (deadline reminders, weekly digest, conversation purge) |
| `SES_NOTIFICATION_TOPIC_ARN` | No | Extra verification for the SES bounce/complaint webhook |
| `WEB_CONCURRENCY` | No | Gunicorn worker count (default 2) — see connection-pool note below |
| `DB_POOL_MIN` / `DB_POOL_MAX` | No | Per-worker connection pool size (defaults 1 / 20) |

**Note on scaling:** the DB connection pool is created per worker process, not shared — so the real ceiling is `WEB_CONCURRENCY × DB_POOL_MAX`. Check your database's `max_connections` before increasing worker count.

## Database migrations

Schema changes go through [Alembic](https://alembic.sqlalchemy.org/) — see
`migrations/README.md` for the full workflow (one-time setup on the existing
database, making a new change, rolling one back). `init_db()` in
`wink/extensions.py` still runs on every startup for backward compatibility,
but new schema changes should be Alembic migrations, not new lines there.

## Running tests

Tests run against a real PostgreSQL database (not mocks), so you'll need one available:

```bash
createdb wink_test
export DATABASE_URL="postgres://postgres:yourpassword@localhost/wink_test"
export SECRET_KEY="test-secret-key"
export ADMIN_EMAIL="admin@utep.edu"

pytest
```

`tests/conftest.py` truncates the relevant tables before every test, so the suite is safe to re-run repeatedly against the same database.

## Deployment

Ships as a Docker container (`Dockerfile`) running `gunicorn` with threaded workers, tuned for I/O-bound workloads (most request time is spent waiting on the database or the Anthropic API, not burning CPU). Health checks are available at:

- `/health` — minimal, unauthenticated, safe for uptime monitors (returns only pass/fail)
- `/health-page` — full diagnostic breakdown (21 checks: database, AI providers, email, storage, scheduled jobs, document parsing, and more), admin-login required

## Privacy & research

WINK is used in a research pilot studying AI-supported academic success. Student interactions may be recorded and analyzed for research purposes — access to research data is restricted, and data is anonymized before use in any publication or presentation. Students explicitly consent to this at registration, separately from the standard Terms of Use. See `/privacy` for full details.

## Known architectural tradeoffs

See [`KNOWN_TRADEOFFS.md`](KNOWN_TRADEOFFS.md) for deliberate design decisions with real, understood costs (large templates/client-side rendering, dual schema-management mechanisms, research data retention, dependency hash-locking) — recorded so they don't get mistaken for oversights or silently rediscovered later.

The CSP (`wink/__init__.py`) is deliberately strict — nonce-based script elements, hash-based script/style attributes computed at startup from the template files themselves (`wink/csp_hashes.py`), no `unsafe-inline` anywhere, `object-src 'none'`, `base-uri 'self'`. That strictness is real security value, but it does mean any new inline event handler or `<style>` block added to a template needs its hash to actually get picked up by `csp_hashes.py`'s startup scan — a handler added without understanding this will silently fail (the browser drops it, CSP blocks it) rather than error loudly. `test_csp_hashes.py` covers this, but it's worth knowing going in rather than debugging a silently-broken button from CSP violations alone.
