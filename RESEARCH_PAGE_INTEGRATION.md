# WINK research infrastructure — what's included and what still needs wiring

Two rounds of changes, both in response to the July 2026 external WINK
review's top finding: no independently demonstrated accuracy rate, and no
trail showing what produced any given answer.

## Files here (drop into your repo at the matching paths)

- `wink/__init__.py` — replaces yours. Registers the new `research` blueprint.
- `wink/extensions.py` — replaces yours. Adds to `init_db()`:
  - `deadlines.status` ('detected' | 'confirmed' | 'corrected' | 'superseded'),
    `deadlines.source_snippet`, `deadlines.confirmed_at`
  - a new `answer_logs` table: one row per chat answer — model, retrieval
    backend, chunk count, document ids, latency, prompt version,
    `conversation_id`/`message_index` (to correlate with student feedback),
    `student_feedback` ('up'/'down'), and `faculty_rating`/`faculty_notes`/
    `rated_by`/`rated_at` (for reviewer scoring).
  - All additive (`ADD COLUMN IF NOT EXISTS` / `CREATE TABLE IF NOT EXISTS`) —
    safe to deploy against your existing database, nothing dropped or renamed.
- `wink/services/deadlines.py` — replaces yours. Adds `source_snippet`
  extraction, `insert_deadlines()`, `set_deadline_status()`,
  `get_deadline_confirmation_stats()`, a `confirmed_only` flag on
  `get_upcoming_deadlines()`, and unconfirmed-deadline language in
  `build_deadlines_context()`.
- `wink/services/research.py` — **new file**. `log_answer()`,
  `record_student_feedback()`, `rate_answer()`, `get_config_snapshot()`,
  `get_answer_log_stats()`, `get_feedback_vs_accuracy_gap()`,
  `get_unrated_sample()`, `get_rated_sample()`.
- `wink/blueprints/research.py` — **new file**. `/research` (admin page),
  `/research/rate-answer` (POST, reviewer rating), `/research/export.json`.
- `templates/research.html` — **new file**. Config snapshot, answer-provenance
  stats, deadline correction-rate, the feedback-vs-accuracy gap, and an
  inline interface to rate unrated answers.
- `templates/calendar.html` — **updated** (your uploaded version, edited in
  place). Each deadline in the day panel now shows a status badge
  (Unconfirmed / Confirmed / Corrected / Dismissed), the source sentence it
  was extracted from, and — for unconfirmed ones — Confirm / Correct /
  Dismiss buttons that call the new `/deadlines/<id>/confirm` route (see #3
  below, which you still need to add).
- `templates/chat.html` — **updated** (your uploaded version, edited in
  place). The highlighted-filename citation now carries a `title` tooltip
  ("Mentioned by name — not an independently verified citation") so it stops
  reading as a verified source check, which the review specifically flagged
  it isn't. No other behavior changed.

## What you still need to wire (not included — those files weren't uploaded)

**1. `blueprints/chat.py`** — right after a `/chat` response finishes, log it,
and mirror the existing thumbs up/down onto the same row:

```python
from ..services.research import log_answer
import time

start_time = time.time()
# ... existing model call, producing full_response_text, conversation_id,
#     saved_messages (the conversation's message list after this turn) ...
log_answer(
    student_id=g.student["id"],
    question=user_message,
    answer_text=full_response_text,
    conversation_id=conversation_id,          # or None
    message_index=len(saved_messages) - 1,    # this answer's position in conversation.messages
    retrieval_backend="neural" if used_neural else ("tfidf" if used_retrieval else "full_context"),
    chunk_count=len(top_chunks) if used_retrieval else 0,
    document_ids=[d["id"] for d in docs],
    latency_ms=int((time.time() - start_time) * 1000),
)
```

`used_neural` / `used_retrieval` / `top_chunks` are whatever
`build_doc_context()` already decided for that turn. If it doesn't currently
return which path it took, the cleanest change is having it return
`(context_text, retrieval_backend, chunk_count)` instead of just the string.

Then in the **existing** `/rate-answer` route (the one `chat.html`'s
`submitFeedback()` already calls with `conversation_id`/`message_index`/
`rating`), add one line alongside whatever it already does:

```python
from ..services.research import record_student_feedback
record_student_feedback(conversation_id, message_index, rating)
```

This is what makes `get_feedback_vs_accuracy_gap()` (shown on the research
page) actually measurable, instead of student satisfaction and faculty
correctness living in two places that can never be compared.

**2. Wherever deadlines currently get inserted** (likely in
`blueprints/documents.py`'s upload route, right after `extract_deadlines()`
is called) — switch the raw `INSERT INTO deadlines` to
`services/deadlines.py`'s new `insert_deadlines(student_id, document_id,
course, items)`, so every new deadline starts at `status='detected'` instead
of bypassing the confirmation contract.

**3. `blueprints/calendar.py`** — add the student-facing route
`templates/calendar.html` now calls:

```python
from flask import request, jsonify, g
from ..security import login_required
from ..services.deadlines import set_deadline_status

@bp.route("/deadlines/<int:deadline_id>/confirm", methods=["POST"])
@login_required
def confirm_deadline(deadline_id):
    data = request.get_json(silent=True) or {}
    updated = set_deadline_status(
        deadline_id, g.student["id"], data.get("status", "confirmed"),
        title=data.get("title"), due_date=data.get("due_date"),
    )
    if not updated:
        return jsonify({"error": "Not found"}), 404
    if updated.get("due_date"):
        updated["due_date"] = updated["due_date"].isoformat()  # Flask's jsonify
        # doesn't ISO-format date objects the way the frontend expects —
        # do it explicitly, same pattern used everywhere else in this app.
    return jsonify(updated)
```

Your existing `/calendar-data` route should already pick up `status` and
`source_snippet` automatically — they come from `get_all_deadlines()` in
`services/deadlines.py`, which this change updated, as long as that route
just serializes whatever `get_all_deadlines()` returns (which every other
deadline-reading route in this app already does).

## Why this exists

- **Provenance**: `answer_logs` records the exact model/retrieval-backend/
  chunks/documents behind every answer, instead of that information only
  ever existing implicitly in code that may have changed by the time anyone
  asks "was this answer any good?".
- **Real accuracy signal**: a faculty reviewer can now score a sample of
  real answers correct/incorrect/unsure on `/research` — thumbs up/down
  alone can't distinguish "satisfying" from "correct," which the review
  called out directly.
- **The gap itself is measurable**: `student_feedback` and `faculty_rating`
  live on the same row, so "confident-sounding wrong answers students
  approved of anyway" is a real, queryable number, not a hypothetical risk.
- **Deadlines become a tracked pipeline**: detected → confirmed/corrected,
  with a source snippet to check against and a real correction-rate number,
  instead of every AI-extracted date being presented with equal confidence.
- **Citation honesty**: the filename highlight in chat now says plainly that
  it's a name mention, not a verified check — a one-line fix for a
  real trust gap while the actual passage-level citation system (a bigger
  change, needs chunk metadata threaded through `/chat`) remains future work.
- Everything is additive — no existing student-facing behavior changes until
  you wire in #1–#3 above, and even then it only affects logging plus how
  confidently the assistant talks about unconfirmed deadlines.

## Grade calculator — rebuilt from scratch

Separately: `wink/services/grades.py`, `wink/blueprints/grades.py`, and
`templates/grades.html` are a full rebuild of the grade calculator from an
earlier session, since the original files were lost and couldn't be
re-uploaded. I only had fragments of the original from conversation search
(a README description, a DB schema snippet, part of the blueprint) — this
is a fresh implementation matching that documented behavior, **not** a
byte-for-byte restore. Also added `grades` to the blueprint registration in
`wink/__init__.py`, the `grading_weights` table to `wink/extensions.py`, and
a "Grades" nav link to `calendar.html` and `chat.html` (the only two
templates I have to edit — add the same link to your other pages'
nav-links block if they don't already have one).

**What it does:** pick a course → "Pull weights from my syllabus" calls
`/extract-grading-weights`, which runs a one-time Haiku extraction (same
pattern as deadline extraction) over that course's uploaded documents and
stores the result via `store_grading_weights()` (full replace-on-save).
The weights render as an editable table — add/remove/rename categories,
autosaves 700ms after your last edit via `/save-grading-weights`. Enter a
score for any category to see the live current-grade recompute (blank
categories are excluded from the weighted average, not counted as zero).
Enter a target overall grade and it solves for the average needed across
whatever's still blank — verified against the worked example from the
original session (20/85, 30/78, 30/blank, 20/95, target 85% → needs
85.33% on the blank category; the formula reproduces that exactly).

**Scores are NOT sent to the server** — they're kept in this browser's
`localStorage`, keyed per student + course, since they're scratch-pad
what-if numbers, not the real grading scheme. Weights ARE saved
server-side, since they represent the actual breakdown and should follow
the student across devices.

If you still have the *original* `grades.py`/`grades.html` anywhere (a
local clone, a zip from that earlier session, git history), prefer those
over this rebuild — they were tested against real Postgres with a fake
Anthropic client in that session, and this rebuild has only been
syntax-checked, not run against a live database.

## Nav links added across every page (this round)

Grades and Research weren't showing up because most pages' nav bars
never had links to them — adding the routes/pages doesn't add them to
every other template's hardcoded nav markup automatically (this app
doesn't extend a shared nav from `base.html` yet, even though `base.html`
exists — see the original review's note on template duplication).

Updated in this pass: `analytics.html`, `dashboard.html`, `documents.html`,
`practice.html`, `calendar.html`, `chat.html`, `grades.html`, `wrapped.html`
— each now has a **Grades** link, and an **Analytics**/**Research** pair
gated behind `{% if s.email == admin_email %}` (matching however each page
already gated Analytics). `research.html` itself had no nav at all before
this — it now has the same nav bar as every other page, so `/research`
also passed `s=g.student`, `admin_email=config.ADMIN_EMAIL`, and
`active="research"` into the template (added to
`wink/blueprints/research.py`'s `render_template()` call).

**If you add a new page later**, the nav link has to be added by hand to
every existing template's nav block — there's no single source of truth
for it yet. Moving every page to actually extend `base.html` (which
already exists in your repo, just isn't used by any of these pages) would
fix that for good, at the cost of a larger one-time refactor.
