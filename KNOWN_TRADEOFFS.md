# Known architectural tradeoffs

Deliberate design decisions with real, understood costs — not oversights.
Recorded here (rather than silently living only in code comments) after
the August 2026 engineering audit, so a future pass doesn't waste time
rediscovering the same tradeoff, and so "not fixed yet" is distinguished
from "wasn't understood."

## Frontend: large per-page templates, heavy client-side `innerHTML`

`analytics.html` (~278KB), `dashboard.html` (~263KB), and `register.html`
(~254KB) embed most of their behavior directly in server-rendered HTML
rather than a separate frontend build. `chat.html` in particular builds
significant UI dynamically via `innerHTML` (message rendering, embeds,
citations — see its own inline comments for the specific precautions
taken: `escapeHtml()`, CSP with no `unsafe-inline`, hash-based script/
style attributes, avoided inline event handlers).

**Why this wasn't refactored in the August 2026 audit pass:** the audit
correctly flagged this as a real architectural cost — maintenance
complexity, page weight, duplicated JS/CSS behavior across templates —
but restructuring it (splitting into components, moving to a real
frontend build step, or introducing a JS framework) is a substantial,
cross-cutting rewrite with real regression risk across every page in the
app. Doing that safely needs its own dedicated pass with visual/
functional regression coverage first, not an incidental fix bundled into
a security/performance-focused audit response. The `innerHTML` usage
specifically was reviewed as part of this audit and NOT classified as an
active XSS vulnerability — the precautions listed above are real and
effective — but it remains a larger attack surface than a framework with
built-in output escaping would present by default.

**If/when this gets addressed:** start with `chat.html` (highest-risk
surface, since it renders AI-generated content) and extract its message-
rendering logic into its own well-tested module before touching layout;
the giant server-rendered templates are a lower-urgency, larger-scope
project.

## Database schema: Alembic + `init_db()` startup mutation, not one authoritative mechanism

`wink/extensions.py`'s `init_db()` runs `CREATE TABLE IF NOT EXISTS` /
`ADD COLUMN IF NOT EXISTS` on every app startup, alongside a separate
Alembic migration chain (`migrations/versions/`) that was introduced
later, with `migrations/versions/a0205eeb64e6_baseline_schema_as_built_by_wink_.py`
as the baseline bringing the already-running database under Alembic's
management.

**Why both still exist:** `init_db()`'s statements are all idempotent
(`IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`), so running both on every
deploy is safe today, and the migration baseline was deliberately
constructed to match what `init_db()` had already built rather than
guessing at a from-scratch schema. Removing `init_db()`'s schema-mutation
entirely and going Alembic-only is the correct long-term direction, but
doing it now — without a chance to verify the baseline migration
produces a byte-for-byte-equivalent schema on a genuinely fresh database
across every environment this deploys to — risks a silent schema drift
bug that's much worse than the current (understood, safe) duplication.
The CI workflow already verifies migrations replay cleanly from scratch
on every push specifically to catch drift early.

**Going forward:** do NOT add new schema changes to `init_db()` — new
schema changes should be new Alembic migrations only, so `init_db()`
gradually becomes purely a startup no-op for anyone running current
migrations, rather than growing further.

## Research data retention after account deletion

Account deletion anonymizes the student's own profile fields, but
conversations, uploaded documents, answer logs, and research activity
records are deliberately retained (not deleted) — see
`wink/services/research.py` and the account-deletion flow in
`wink/blueprints/auth.py`.

**This is a policy decision, not a bug**, and its correctness depends
entirely on `/privacy` and the registration-time research consent
language accurately describing this retention model to students at the
point they agree to it. If that language and this code ever diverge,
that's a compliance problem independent of anything in this file — check
`/privacy` and the registration consent copy stay synchronized with
actual retention behavior any time either changes.

Separately, `retrieved_context` (added to `answer_logs` for research
reproducibility — see migration `6535ed24cbc8`) stores a verbatim
snapshot of what the AI actually saw for each answer, which is valuable
for research but is also another persistent copy of potentially
sensitive academic material, increasing the retained-data footprint
described above. This was an intentional reproducibility tradeoff, not
an oversight.

## Dependency hash-locking

`requirements.txt` pins exact versions but does not include cryptographic
hashes (`pip install --require-hashes` is not used). This means the build
is reproducible at the version level but not the strongest supply-chain
level.

**Why this wasn't added:** generating a hash-locked file requires
resolving the FULL transitive dependency tree (`pip-compile
--generate-hashes` or equivalent) — not just the top-level pins already
in `requirements.txt`. That resolution was attempted and did not
complete in a reasonable time in the environment available for the
August 2026 audit pass. Shipping a partial hash file is actively unsafe:
`--require-hashes` fails the install outright unless EVERY dependency,
including transitive ones, has a hash — a partial file would break
deployment rather than improve security.

**Recommended follow-up:** run `pip-compile --generate-hashes` in an
environment with a longer time budget (or split it into smaller batches
per top-level dependency), verify the resulting lockfile installs
cleanly in a fresh container matching the Dockerfile's base image, and
only then switch the Dockerfile's install step to
`pip install --require-hashes -r requirements.lock.txt`. The CI
dependency-vulnerability scan (`pip-audit`, added alongside this audit
pass) partially covers the same risk surface in the meantime — it won't
catch a compromised/tampered package with a valid version number, but it
does catch known-vulnerable versions, which was the more immediate gap.
