# WINK — What I Need to Know

[![Tests](https://github.com/lauratrevino/WINK-temp/actions/workflows/tests.yml/badge.svg)](https://github.com/lauratrevino/WINK-temp/actions/workflows/tests.yml)

WINK is an AI-powered academic support platform for college students. Students upload their own course materials (syllabi, calendars, assignment instructions, notes) and WINK answers questions, tracks deadlines, generates practice questions, and helps with grade calculations — all grounded in what they've actually uploaded, not generic knowledge.

WINK was created by Dr. Laura L. Trevino at the University of Texas at El Paso as part of a research pilot studying how AI can support student success during the first year of college.

## What it does

- **Chat** — Ask questions about course material, with rich responses: embedded maps, photos, and diagrams (Mermaid) render inline where they help. The AI names the specific uploaded document it drew from; conversations are saved, exportable, and shareable via a read-only link.
- **Deadlines & Calendar** — Automatically extracts deadlines from uploaded documents, with a full calendar view, conflict detection, and reminder emails. Every date the AI discusses is labeled with a precomputed weekday and relative-day description, so it's never left to work that out on its own.
- **Practice & Assessment** — Generates study materials, flashcards, and quizzes from course material; an Assessment Quiz mode checks current knowledge and builds a personalized study plan.
- **Grade Calculator** — Pulls grading breakdowns from an uploaded syllabus and calculates what's needed on remaining work.
- **Progress & Wrapped** — Personal engagement stats and a semester-in-review page.
- **Admin tools** — Analytics dashboard, per-student research view, a live health/diagnostics page, and a research-data export pipeline (raw + faculty-rated) for studying answer accuracy.

## Tech stack

- **Backend:** Flask 3, PostgreSQL (via `psycopg2`, connection-pooled)
- **AI:** Anthropic Claude for chat/generation; Voyage AI for semantic document search (optional — falls back to TF-IDF via scikit-learn if not configured)
- **Document parsing:** `pypdf`, `python-docx`, `python-pptx`, `openpyxl`, `pytesseract` (OCR)
- **Email:** Amazon SES via SMTP, with SNS-based bounce/complaint handling
- **Frontend:** Server-rendered Jinja2 templates, vanilla JS (no framework/build step)
- **Security:** Hash-based Content-Security-Policy (no `unsafe-inline`), CSRF protection, DB-backed rate limiting keyed by IP+account, hashed password-reset tokens, TOTP two-factor authentication for admin access
- **Tests:** `pytest`, run against a real PostgreSQL database (not mocked) — 148 tests covering registration, document upload/parsing, chat, retrieval ranking, spaced repetition, concurrency, CSP correctness, resource-bounding/performance regressions, rate limiting, and an AI-quality golden dataset

## Project structure

```
wink/
├── blueprints/         # Route handlers, one file per feature area (auth, chat, documents, ...)
├── services/           # Business logic — document parsing, retrieval, grading, research tracking, etc.
├── config.py           # All configuration, read from environment variables
├── extensions.py       # DB connection pool, CSRF, Anthropic/Voyage clients, schema bootstrap
├── security.py         # Auth decorators, rate limiting
├── timeutil.py          # Timezone resolution, deterministic date/weekday labeling
└── errors.py            # Centralized error logging

templates/              # Jinja2 templates, one per page
static/                 # CSS, JS, images
tests/                  # pytest suite (runs against a real Postgres DB)
migrations/             # Alembic schema migrations
scripts/                # Scheduler launchers for reminder/digest/cleanup jobs (see scripts/README.md)
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

The database schema is created automatically on first run (`wink/extensions.py:init_db()` — every statement is `CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`, so it's safe to run repeatedly). New schema changes go through Alembic instead — see `migrations/README.md`.

### Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SECRET_KEY` | Yes | Flask session/CSRF signing key — must be fixed in production |
| `ADMIN_EMAIL` | Yes | Which account gets admin access (comma-separated `ADMIN_EMAILS` for more than one) |
| `ANTHROPIC_API_KEY` | Yes (for chat) | Chat/generation model |
| `VOYAGE_API_KEY` | No | Enables semantic document search; falls back to TF-IDF if unset |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASS` | No | Outbound email (verification, reminders); logs to console instead if unset |
| `CRON_SECRET` | No | Authorizes the scheduled jobs in `scripts/` (deadline reminders, weekly digest, cleanup) |
| `SES_NOTIFICATION_TOPIC_ARN` | No | Extra verification for the SES bounce/complaint webhook |
| `WEB_CONCURRENCY` | No | Gunicorn worker count (default 2) |
| `DB_POOL_MIN` / `DB_POOL_MAX` | No | Per-worker connection pool size (defaults 1 / 20) — the real ceiling on concurrent DB connections is `WEB_CONCURRENCY × DB_POOL_MAX` |

## Database migrations

Schema changes go through [Alembic](https://alembic.sqlalchemy.org/) — see `migrations/README.md` for the full workflow: checking a database's current state, applying a pending migration safely, making a new change, and rolling one back.

## Running tests

Tests run against a real PostgreSQL database (not mocks):

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
- `/health-page` — full diagnostic breakdown (database, AI providers, email, storage, scheduled jobs, document parsing, and more), admin-login required

Scheduled jobs (deadline reminders, weekly digest, expired-demo and old-conversation cleanup) are triggered externally via the scripts in `scripts/` — see `scripts/README.md` for the recommended schedule and how to wire up a scheduler.

## Security notes

- Content-Security-Policy is strict: nonce-based script/style elements, hash-based inline event-handler and style attributes computed at startup from the template files themselves (`wink/csp_hashes.py`), no `unsafe-inline` anywhere, `object-src 'none'`, `base-uri 'self'`. A new inline event handler or `<style>` block added to a template is picked up automatically by the startup scan; markup generated dynamically at runtime (e.g., a third-party library emitting its own `<style>` tag, as Mermaid does for diagrams) needs its nonce set explicitly by the JS that inserts it, since the startup scan only sees the static template files.
- Rate limiting on login, registration, and password reset is keyed by IP **and** account together, not IP alone, so one address can't exhaust another account's attempt budget just by sharing a network. Registration additionally carries a coarser per-IP ceiling to stop bulk fake-account creation.
- The database schema is managed by Alembic going forward; `init_db()` in `wink/extensions.py` is a frozen, idempotent bootstrap kept only for backward compatibility and should not gain new schema changes.

## Privacy & research

WINK is used in a research pilot studying AI-supported academic success (IRB protocol 26-07-899; up to 50 participants across a WINK-enabled and a comparison course section). Student interactions may be recorded and analyzed for research purposes — access to research data is restricted, and data is anonymized before use in any publication or presentation. Students explicitly consent to this at registration, separately from the standard Terms of Use. See `/privacy` for full details.
