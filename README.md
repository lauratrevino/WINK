# WINK — structure notes

This replaces the single 2,300-line `app.py` with a small package. Behavior,
routes, and URLs are unchanged — `gunicorn app:app` still works exactly as
before, and every URL a template or JS `fetch()` call hits (`/upload`,
`/chat`, `/dashboard`, etc.) is identical to what it was.

Templates and static assets are included and verified: every template
renders successfully with representative data, and static file serving
(`/static/WINK.jpeg`, etc.) works — checked with a live Flask test client,
not just by inspection.

## Practice tests from a temporary handout (this pass)

Answering a direct question: yes, now — a student can upload a handout
via `/upload`'s existing `temporary=true` flag (already built — extracts
the text, hands it back to the client, never saves it anywhere) and feed
that extracted content straight into `/generate-practice` via a new
`temp_material` field, without that course needing any permanent upload
at all. This wasn't previously wired together — the temporary-upload path
only ever fed into `/chat`'s per-message context, and `/generate-practice`
only ever read from permanently saved documents. Now the two connect.

`temp_material` can also be combined with a course's existing permanent
uploads in the same request — a student with a saved syllabus can still
toss in a one-off pop-quiz handout for a single practice set without
saving it.

**What's still true:** only the *generated questions* are stored (for
spaced repetition) — the original handout text itself is never written to
the `documents` table or anywhere else, verified directly: after
generating questions from a temporary handout, the test confirms zero
rows exist in `documents` for that student while the questions themselves
are sitting in `practice_questions`.



## Five more features (this pass)

All verified against real Postgres, same methodology as everything else —
11 new tests, all passing on first real run against the live database.

**1. Source citations.** The chat system prompt already labeled every
document `[DOCUMENT N] name.ext` internally, but only told the model to
say "your documents" generically. Now it's told to name the actual file
("According to Spring2026Syllabus.docx...") and cite every document it
actually drew from, not just the first. Prompt-only change — verified the
instruction text is present in the assembled prompt; live model compliance
can't be checked without a real API key, same caveat as the crisis-search
instruction from earlier.

**2. Deadline-conflict detection.** New `detect_deadline_conflicts()` in
`services/deadlines.py` clusters a student's extracted deadlines by date
proximity (default: 5+ days apart breaks a cluster, 3+ items makes it
worth flagging) and feeds any cluster found into the chat context so the
model can proactively mention a busy stretch — "you have three things due
within four days of each other" — instead of a student finding out the
hard way. New `GET /deadline-conflicts` endpoint for a future dashboard
widget. Verified with real inserted deadline rows: a real 3-item cluster
within 5 days is detected, three deadlines spread 20+ days apart correctly
produce no false positive.

**3. Spaced repetition on practice questions.** `/generate-practice`
(built last pass) now actually stores what it generates instead of
returning it once and discarding it. New `practice_questions` table, a new
`schedule_next_review()` (correct answer triples the review interval,
capped at 60 days; any wrong answer resets straight back to 1 day — a
simple, well-established scheme, not full SM-2, since the evidence for
spaced repetition's benefit holds across a wide range of interval
schemes), and two new endpoints: `POST /practice-attempt` (record
right/wrong, reschedule) and `GET /practice-review` (what's actually due
today). Verified end-to-end for real: generate a question from the real
uploaded syllabus, confirm it's due today, answer it correctly, confirm
its interval jumps to 3 days and it's no longer due. Also verified a
student can't record an attempt against another student's question
(404, not silently ignored).

**4. Answer feedback (thumbs up/down).** New `POST /rate-answer` —
reuses the existing events table rather than adding a new one (logged the
same way `question_asked`/`answer_given` already are). Aggregated into
`compute_engagement_insights()` as `answer_feedback: {up, down,
positive_pct}`, computed with a single Postgres aggregate query (`payload::json->>'rating'`),
consistent with how every other analytics number here is computed rather
than pulling rows into Python. Verified with real recorded ratings and a
real aggregate query.

**5. Accessibility — one real, verified fix.** Audited every template for
existing `aria-*`/`alt` coverage first (there's already a fair amount).
Found a genuine, meaningful gap: the toast notification had
`aria-live="polite"` but the actual chat message container — where
streamed AI answers actually appear — had none, so a screen reader
wouldn't announce new answers at all. Added `role="log"
aria-live="polite" aria-atomic="false"` to that one container in
`chat.html` and confirmed the page still renders correctly afterward.

**What's still not done, honestly:** no UI for any of this — no way to
mark an upload as an assessment, review due practice questions, see
deadline conflicts, or tap a thumbs-up/down from the dashboard/chat
templates themselves; all five are real, tested, working APIs with no
front-end yet, same limitation noted for the language preference and
practice-question features above. Accessibility beyond the one fix above
— adjustable text size, a read-aloud button, a full audit of the
~70 `onclick=` handlers for keyboard-navigability — is real, valuable work
that wasn't attempted here; template edits at that scale on files this
large, without the ability to visually render them in a browser, is a
different risk profile than the single-attribute fix actually made.



## Practice question generation from student uploads (this pass)

New route: `POST /generate-practice` — generates new practice questions
from a student's own uploaded course material for a specific course.

If a student has also uploaded a real past exam/quiz/study guide and
tagged it `doc_type=assessment` at upload time (new optional field on
`/upload`, defaults to `material` — no existing upload behavior changes
unless this is explicitly sent), that assessment is used as a **style
reference only**: question format, difficulty, phrasing conventions. It is
never used as the source of facts for the generated questions, and the
model is explicitly instructed not to reuse or lightly reword any actual
question from it — verified directly, not just instructed and hoped for:
a real test checks that both the material and the sample assessment reach
the model, but that the system prompt's no-reuse instruction is present
in what's actually sent.

New `doc_type` column on `documents` (default `'material'`). New
`services/practice.py` with the generation logic and its own content
budgets (`PRACTICE_MATERIAL_MAX_CHARS`, `PRACTICE_ASSESSMENT_MAX_CHARS`).
Rate-limited (5 generations per 10 minutes per student) since each call is
a real, non-trivial model request.

Tested against real Postgres and the real uploaded syllabus, with a fake
Anthropic client returning realistic structured JSON (no real API cost):
confirms the actual syllabus content reaches the model, confirms an
assessment-tagged upload is included but paired with the no-reuse
instruction, confirms an invalid `doc_type` is rejected at upload time,
and confirms a course with no uploaded material gets a clear error instead
of an empty or confusing response.

**What this is not:** there's no UI for any of this yet — no way to mark
an upload as an assessment from the documents page, and no way to view or
practice the generated questions anywhere except by calling the API
directly. Both are template work (`documents.html`, and a new results
view) that wasn't done this pass, same as the language-preference dropdown
noted above.



## Multi-language support (this pass)

Two modes, no template changes required:

1. **Auto-detect (the default for everyone, right now, with zero setup):**
   the chat system prompt tells the model to reply in whatever language the
   student writes their message in — Spanish in, Spanish out; English in,
   English out; even mid-conversation if it changes. Claude is natively
   fluent across languages, so this needed no new infrastructure, just an
   instruction.
2. **Explicit override:** a student can set a `preferred_language` (English
   or Spanish for now — see `PREFERRED_LANGUAGES` in `config.py`, a curated
   list like `CLASSIFICATIONS`/`MAJORS`, trivial to extend) via
   `/update-profile`, which makes every reply come back in that language
   regardless of what language they type in. New `preferred_language`
   column on `students`, defaults to `''` (auto-detect).

No dashboard/profile template was touched — there's no dropdown for this
yet, only the backend and API support. Setting a preference today requires
a direct API call (`POST /update-profile` with `preferred_language` in the
JSON body); adding a UI control for it is a small, separate follow-up
whenever there's appetite to edit `dashboard.html`. Verified against real
Postgres: the column defaults correctly, a preference set via the API
persists and round-trips, an unsupported value is rejected, and a later
profile update that doesn't mention the field at all doesn't silently
reset it back to auto-detect.

**What this is not:** full UI localization (translating every button,
label, and page in the templates) — a much larger, separate undertaking
across ~12,500 lines of template markup this pass didn't touch. What's
built here covers the part that matters most for a support tool like
this: the actual tutoring conversation, in the student's own language.



## Crisis resources in the chat system prompt (this pass)

Added explicit crisis-safety instructions to `/chat`'s system prompt
(`wink/blueprints/chat.py`) — takes priority over everything else in the
prompt. If a student's message suggests they may be in crisis (suicidal
ideation, self-harm, an unsafe situation), the model is told to respond
with care first, not treat it as an academic question, not ask probing
questions, and give crisis resources directly rather than waiting to be
asked.

Resources are university-aware: UTEP students get UTEP's actual verified
numbers — the Counseling and Psychological Services Crisis Line ((915)
747-5302, 24/7 including after hours and holidays) and UTEP's separate
Mental Health Crisis Line ((915) 779-1800), both confirmed current
directly from utep.edu, plus the national 988 Suicide & Crisis Lifeline
(confirmed still active and current as of mid-2026). Students at any other
school get 988 immediately, and the model is told to use the web_search
tool right then to find that school's actual campus crisis line and give
the specific number directly — not to tell the student to go search for
it themselves, and not to guess a number from its own training data if
the search doesn't turn up a clear answer (988 alone is a complete,
correct response either way). This was a real gap caught by a direct
follow-up question, not something built in from the start: the first
version only told the model to suggest the student search for their own
school's number, which adds friction at exactly the wrong moment and
risks the model answering from stale memorized knowledge instead.

**Caveat that still applies:** everything above was verified by checking
that the prompt text itself assembles correctly for both a UTEP and a
non-UTEP student. Whether the live model actually follows the "search
before answering" instruction correctly in the moment — and finds a
genuinely correct number — can't be verified from this sandbox, which has
no real Anthropic API key or live model access. Worth an early real test
once this is deployed, precisely because it's the one place where "the
prompt looks right" isn't the same guarantee as everywhere else in this
codebase that had a real test behind it.



## Retrieval (this pass) — real answers stay accurate as material piles up

**The problem this fixes:** the document-context budget (`MAX_DOC_CONTEXT_CHARS`)
used to be divided *evenly* across every uploaded document regardless of
size, and whatever didn't fit was silently cut off the end — with no
regard for whether the cut part was what the student actually asked about.
That's backwards for a research tool where answer accuracy is the first
priority: it means the app gets *less* reliable exactly as a student uses
it more (uploads more classes' worth of material).

**The fix, verified against your real uploaded syllabi, not synthetic
test data:**

1. **When everything fits, nothing is touched.** If a student's total
   uploaded content is under the budget — the common case — every
   document is included in full, exactly as before this change, just
   without the unnecessary even-division truncation that used to happen
   even when it wasn't needed. Verified: uploading the real
   `Spring2026Syllabus.docx` (21,747 characters) produces zero truncation
   in the resulting context.
2. **Only once the total genuinely exceeds the budget does retrieval kick
   in** — each document is chunked (`services/retrieval.py`) into
   ~1,000-character overlapping pieces at upload time, and at question
   time, the chunks most relevant to *that specific question* are pulled
   in, not an arbitrary even slice of every document. New table:
   `document_chunks`, cascade-deleted automatically when a document is
   deleted (verified with a real Postgres foreign key, not assumed).

**Embedding backend — the honest tradeoff.** This ships with TF-IDF
(`scikit-learn`), which needs no model download and runs fully offline —
verified end-to-end against your real syllabus. TF-IDF ranks by literal
word overlap, so it's very good at direct-vocabulary questions and weaker
at pure paraphrase. This was caught directly, not assumed: asking *"What
is the late work policy?"* against the real syllabus correctly surfaced
the actual "Late Work" section, but asking *"What textbook is required for
this course?"* initially missed the "Required Text" section entirely — the
syllabus never uses the word "textbook." Root-caused (confirmed the
document really doesn't contain that word anywhere near the answer), then
mitigated with a small curated academic-vocabulary synonym expansion
(textbook↔required text, midterm↔exam, deadline↔due date, and a dozen
similar pairs — see `_ACADEMIC_SYNONYMS` in `services/retrieval.py`).
Re-tested with a proper controlled comparison after that fix: helped in 2
of 5 real test questions, neutral (no regression) in 2, genuinely
ambiguous in 1 — not a full fix, a real, measured, partial one.

**Upgrading retrieval accuracy.** The actual fix for the paraphrase gap is
a neural embedding model (e.g. `sentence-transformers`, or Voyage AI,
which Anthropic recommends for embeddings) instead of TF-IDF — it
understands "textbook" and "required reading" are related without being
told so explicitly. That wasn't wired in here because doing so requires
downloading model weights from huggingface.co at runtime, which wasn't
reachable to verify from the sandbox this was built in — shipping it
unverified didn't seem right for a research tool where accuracy is the
priority. The seam for it is `_rank_neural()` in `services/retrieval.py`
— `rank_chunks()` already tries it first and falls back to TF-IDF, so
wiring in a real model later is a self-contained change to that one
function, not a rearchitecture.

**What was not done:** the top-N cutoffs (`RETRIEVAL_TOP_N_STUDENT_DOCS`
= 25, `RETRIEVAL_TOP_N_GLOBAL_DOCS` = 12 chunks) are reasonable defaults,
not tuned against real usage data — there isn't any yet. Worth revisiting
once real students have used this for a while and you can see which
questions come back with "I don't see that in your documents" when the
answer really was uploaded.



## Layout

```
app.py                  entry point: from wink import create_app; app = create_app()
wink/
  __init__.py            app factory: config, security headers, CSP nonce setup,
                          blueprint registration, init_db() call
  config.py               every env-derived constant, in one place
  extensions.py            DB pool, Anthropic client, CSRF — built once, reused everywhere
  security.py              current_student(), login/admin decorators, rate limiting,
                           file-signature validation
  services/
    email.py                SMTP sending
    documents.py             text extraction (incl. OCR), doc-context building,
                             get_docs/get_global_docs (with caching)
    deadlines.py              deadline extraction + queries
    analytics.py               event logging + admin dashboard queries
  blueprints/
    auth.py                   register, login, logout, verify-email, password reset
    dashboard.py                /dashboard, /update-profile
    documents.py                 /documents page, upload/delete, admin global-docs routes
    calendar.py                   /deadlines, /calendar-*, reprocess, reminder emails
    chat.py                        /chat-page, /chat, conversation CRUD/export/share
    admin.py                        /analytics-*, /student-conversations, suspend/reactivate
    misc.py                          landing page, /health
```

A change to one concern now touches one file — e.g. editing how deadlines
are extracted means editing `services/deadlines.py`, not scrolling through
auth and chat code to find the right 40 lines in a 2,300-line file.

## What changed beyond the split

**Scalability (hundreds of students, multiple schools):**
- DB pool size is now configurable via `DB_POOL_MIN` / `DB_POOL_MAX` env vars
  (default 1/20) instead of hardcoded — tune it to your actual worker/thread
  count and expected concurrent load.
- `get_global_docs()` (queried on *every* chat message, every student) is now
  cached per-university for `GLOBAL_DOCS_CACHE_TTL_SECONDS` (default 60s) —
  a big cut in repeat, identical, read-only queries at scale. Admin
  upload/delete of a reference doc invalidates that worker's cache
  immediately; other workers pick up the change within the TTL.
- The two admin analytics endpoints (`/analytics-data`, `/analytics-data-full`)
  no longer run 4 correlated subqueries per student row — `get_student_summaries()`
  in `services/analytics.py` does it as two grouped aggregates joined once,
  which scales with total events/documents instead of students × columns.
- Gunicorn worker count is now driven by `WEB_CONCURRENCY` (many PaaS
  providers, e.g. Render, set this automatically based on instance size) —
  see the Dockerfile. Workers also recycle periodically (`--max-requests`)
  to bound long-run memory growth.

**OCR (previously absent):** `services/documents.py` now runs image uploads
through `pytesseract` if it and the `tesseract-ocr` system package are
present (both added to `requirements.txt` / `Dockerfile`). Falls back to the
old placeholder behavior if OCR isn't available in the running environment,
so this can't crash the app if a deploy target is missing the system
package.

**CSP — tightened, not just prepared.** Every template's inline `<script>`
and `<style>` block now carries `nonce="{{ csp_nonce() }}"` (generated fresh
per request in `wink/__init__.py`), and the CSP header uses the CSP3
`script-src-elem`/`style-src-elem` directives to require that nonce. That
blocks the highest-value XSS payload — an attacker injecting a brand-new
`<script src="https://evil.com/x.js">` or `<style>` tag — even if they find
an injection point elsewhere in the app.

What's *not* tightened: the templates use inline event-handler attributes
(`onclick=...`, 70+ of them across the dashboard/documents/chat/analytics
pages) and inline `style="..."` attributes extensively. CSP has no nonce
mechanism for attributes at all — only `'unsafe-inline'` or a fragile
per-handler hash — so `script-src-attr`/`style-src-attr` are left on
`'unsafe-inline'` rather than rewriting every handler to
`addEventListener`/CSS classes as a silent side effect of this pass. That's
a real, deliberate follow-up job, not a gap that was missed.

**Not changed:** all database schema, all route paths, all response shapes,
rate limits, cost controls, and the chat system prompt are identical to
before.

**Note:** `templates/base.html` and `templates/index.html` were included in
the upload but aren't referenced by any route (`grep -r "extends\|base.html\|index.html"`
against the blueprints turns up nothing) — they're carried over as-is in
case they're used elsewhere, but nothing in this app currently renders them.

## Dead-code / duplication audit

Ran `ruff check --select=F` (unused imports, unused variables, redefinitions)
across the whole package — zero findings. Also checked by hand for things
that kind of tool can't catch:

- **`db_release(conn)`** was a documented no-op (real cleanup happens once
  per request via the teardown handler in `extensions.py`) but was still
  being called at all ~47 sites that touch the database, left over from
  mechanically preserving the original file's call sites. Removed the
  function and every call site — `cur.close()` alone is enough now.
- **`CSRF_AVAILABLE`** in `extensions.py` was set but never read anywhere.
  Removed.
- **`login_required`** existed but wasn't actually applied anywhere in an
  earlier pass — 18 routes across 5 blueprints still did the manual
  `s = current_student(); if not s: ...` check by hand. Converted all of
  them (added a `page_login_required` variant for the page-redirect cases);
  `current_student()` is now called from exactly one place in the entire
  codebase — inside `security.py`'s own decorators.
- **Repeated logic:** the same conversation-messages-parsing expression
  (`safe_payload(x["messages"]) if isinstance(...) else (...)`) appeared 5
  times in `chat.py`. Factored into `parse_conversation_messages()` in
  `services/analytics.py`.
- **Two routes were computing/passing data their templates never use:**
  `chat_page()` ran a real DB query (`get_docs()`) for a `docs` variable
  `chat.html` never references — removed the query, not just the kwarg.
  `analytics_page()` passed `admin_email`, which `analytics.html` never
  references either — removed.

Every change here was re-verified against a live Flask test client
afterward (all routes, auth gating, and page rendering with mock data),
not just re-read.

## Real testing (this pass) — and a correction

Everything up to this point had been verified with mocked database
connections and a fake Anthropic client. This pass installed a real local
Postgres and ran the actual app against it — real schema creation, real
rows, real files.

**A correction:** the "streaming chat responses hold a DB connection open
for the whole response" finding from the previous pass was wrong, and the
fix built for it (`release_db_early()`) has been removed. Direct,
instrumented testing against the real database showed that Flask already
releases the connection back to the pool as soon as the view function
returns — *before* the streaming generator's slow part runs — with or
without that explicit call. `stream_with_context`'s job is only to let the
generator still read `request`/`g`/`session` after the original context is
torn down; it doesn't keep the original connection checked out. Confirmed
by running the exact same test with and without the extra code and getting
identical results. The explanation now lives in `get_db()`'s docstring in
`wink/extensions.py`, verified rather than assumed.

What the real-DB test suite (`tests/`) actually covers:
- Full register → login → upload → chat → admin-dashboard flow against
  live Postgres, not mocked cursors
- Uploading the actual `.docx` and `.pdf` course files and asserting real
  text was extracted (not just "no exception was raised")
- Ownership checks: one student cannot delete another's document by ID
- The document cap, re-upload/replace behavior, and admin account
  suspension, all verified against real rows
- A concurrency test: 8 simultaneous slow-streaming chat requests against
  a deliberately undersized (3-connection) pool all complete in ~1
  stream's worth of time rather than queuing — proof the pool behaves
  correctly under load, via Flask's normal request lifecycle
- Run `pip install pytest && pytest tests/` against a real
  `DATABASE_URL` to reproduce any of this yourself

Also fixed while running these: `datetime.utcnow()` (used in 5 places —
password reset expiry, deadline extraction's "today", conversation message
timestamps) is deprecated in Python 3.12+ and was throwing
`DeprecationWarning` on every real test run. Replaced with a small
timezone-safe helper (`wink/timeutil.py`); the full suite now runs clean
under `-W error::DeprecationWarning`.

**Also checked, found clean:** the Dockerfile's `tesseract-ocr` package
was confirmed to actually exist in the base image's apt repository (not
just assumed); `requirements.txt` was confirmed to resolve and install
with no version conflicts. Every template's JavaScript was scanned for
`getElementById()` calls and `onclick=` handlers referencing something
that doesn't exist in that file — 6 candidates came up, all 6 turned out
to be false positives on manual inspection (a lazily-created toast/modal
element, and an inline `if(...)` the scanner mistook for a function call),
not real bugs.

**Still not done, and can't be from here:** a true production load test
against a deployed instance, and a manual click-through in an actual
browser. The real-Postgres test suite and the gunicorn/HTTP validation
below are a large step up from mocks, but neither is a substitute for a
staging deploy before real students see this. (Docker itself is covered in
its own section below — short version: got much further than expected,
still couldn't finish it from inside this sandbox.)

## Real gunicorn + real HTTP (this pass)

Went one step further than the Flask test client: installed gunicorn and
ran the *actual* production command from the Dockerfile —
`gunicorn --workers 2 --worker-class gthread --threads 8 --timeout 120
app:app` — as a real process, against the real Postgres instance, and hit
it with real `curl` requests over an actual socket (not Python function
calls). Confirmed:
- Both worker processes boot cleanly and `/health` reports genuine DB
  connectivity from a separate OS process (`{"db": true}`)
- A full register → cookie → dashboard flow works over real HTTP, including
  fetching a real CSRF token from the rendered page and submitting it back
  — not bypassed, not mocked
- The resulting student row is genuinely sitting in Postgres afterward
- CSP and HSTS headers are present on real production-server responses

**On the Docker image itself:** I attempted to actually run `docker build`
on the Dockerfile in this pass. A real Docker daemon does start in this
sandbox, and the build genuinely begins (build context sent, Step 1
reached) — but pulling the `python:3.11-slim` base image fails with `403
Forbidden`. This sandbox's network access is restricted to a specific
allowlist (PyPI, npm, GitHub, Ubuntu's package archives, a few others);
no container registry is on it, and that isn't something fixable from
inside the sandbox. The Dockerfile's individual ingredients have all been
verified independently (the `tesseract-ocr` package exists in the apt repo
it pulls from, `requirements.txt` resolves with zero conflicts, and now the
exact application command it runs has been proven working against a real
database over real HTTP) — but the literal `docker build` has never
completed, and the first real one should happen on whatever platform you
deploy to, with a normal internet connection.



## Speed and security fixes (an earlier pass)

Each of these was verified with a targeted test against a mocked DB/API
client, not just read over — details below. (One item that was originally
listed here — "streaming chat responses hold a DB connection open" — turned
out to be based on a mistaken understanding of Flask's `stream_with_context`
and has been retracted; see "Real testing (this pass) — and a correction"
above for the full explanation.)

- **`reprocess_deadlines()` no longer calls the Anthropic API sequentially,
  once per document.** A student at the 20-document cap could hit up to 20
  sequential calls, long enough to risk gunicorn's 120s request timeout.
  Now runs them concurrently (bounded to 5 at a time — see
  `MAX_REPROCESS_WORKERS` in `calendar.py`) since each call is purely
  I/O-bound. Verified: 6 simulated 0.3s calls finished in ~0.6s instead of
  ~1.8s, and a simulated failure on one document didn't affect the others.
- **`get_docs()` — a student's own document list — is now cached** the same
  way `get_global_docs()` already was, with a short TTL
  (`STUDENT_DOCS_CACHE_TTL_SECONDS`, default 20s) and immediate invalidation
  on that worker after any upload/delete. It's queried on every single chat
  message, making it the highest-frequency query in the app.
- **Added the `Strict-Transport-Security` header** (skipped in local dev,
  same gate as `SESSION_COOKIE_SECURE`).
- **Added `ProxyFix`**, configurable via `TRUSTED_PROXY_HOPS` (default 1).
  Without it, behind a platform load balancer, `request.remote_addr` is the
  proxy's own IP for every visitor — silently making the per-IP rate limits
  on login/register/forgot-password apply to everyone combined instead of
  each visitor individually — and `url_for(..., _external=True)` links
  (email verification, password reset) can come out as `http://` instead of
  `https://`. Verified against a realistic single-hop `X-Forwarded-For`/
  `X-Forwarded-Proto` header: `remote_addr` and `scheme` both resolve
  correctly now; without any header (local testing) it falls back to the
  direct connection as before.
- **Closed a minor timing side-channel on `/login`.** A failed login for a
  nonexistent email used to return faster than one for a real email with
  the wrong password, because the (deliberately slow) password-hash check
  only ran when the account existed — a small side-channel for guessing
  registered emails. Now runs a dummy hash comparison either way. Verified:
  both paths take comparable time (~120-130ms) instead of one being
  dramatically faster.

**Deliberately not changed:** `/register`'s "Account already exists —
please log in" message does reveal whether an email is already registered
(unlike `/forgot-password`, which deliberately gives the same response
either way). Silencing it would mean a student who mistypes their email
during signup gets a vague, unhelpful error instead of being told to log
in — a real support-burden trade-off for a very low-value target in an
academic-support-tool threat model. Left as-is; flagging it here rather
than changing it silently in case that trade-off should go the other way
for this deployment.
