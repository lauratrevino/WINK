"""
All environment-derived configuration lives here, and nowhere else. This is
plain constants — no Flask `app` object, no DB connections, nothing that
does work at import time beyond reading env vars. Every other module reads
what it needs from here instead of calling os.environ.get() itself, so
"what does this app do differently in prod vs. dev" has exactly one place
to look.
"""
import os
import re
import secrets

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
ALLOWED_EXT = {"pdf", "docx", "txt", "pptx", "xlsx", "png", "jpg", "jpeg"}

# ── Document parsing resource limits ──────────────────────────
# docx/pptx/xlsx are all ZIP containers — a maliciously crafted archive can
# pass the file-signature check (it really is a valid ZIP) and then expand
# to consume far more CPU/memory than its on-disk size suggests (a "zip
# bomb"). These bound the worst case using only the ZIP central directory
# (cheap to read, doesn't require decompressing anything) before handing
# the file to python-docx/pptx/openpyxl at all.
MAX_ZIP_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  # 200MB total across all entries
MAX_ZIP_COMPRESSION_RATIO = 100  # a legitimate Office file rarely exceeds ~20:1
MAX_ZIP_ENTRY_COUNT = 5000  # legitimate Office files have dozens to low hundreds
MAX_PDF_PAGES = 500  # bounds worst-case time in the per-page extraction loop below
# extract_text() falls back to a placeholder for these if OCR isn't available
# in the running environment (missing tesseract binary) — see
# services/documents.py. Both upload paths use this to warn the student
# clearly instead of silently accepting the file and leaving them to
# discover later that questions about it don't work.
IMAGE_EXTS_NO_OCR = {"png", "jpg", "jpeg"}

DB_URL = os.environ.get("DATABASE_URL", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "lhall@utep.edu").lower()
MAX_DOCS_PER_STUDENT = 20
# No whitespace/control characters anywhere in the address, and must end in
# .edu — deliberately simple rather than a fully RFC-5322-compliant pattern,
# since the goal here is rejecting injection payloads, not exhaustively
# validating every legal email format.
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.edu$")
# Password reset has no real email provider wired up yet (see forgot_password
# in blueprints/auth.py). Never expose the raw reset link in an HTTP response
# in production — that turns "forgot password" into a way to take over ANY
# account just by knowing their email. Only set this to true for local/dev
# testing.
DEBUG_SHOW_RESET_LINKS = os.environ.get("DEBUG_SHOW_RESET_LINKS", "false").lower() == "true"

# ── Email ─────────────────────────────────────────────────────
# Standard SMTP config. Works with SendGrid, Mailgun, Amazon SES (SMTP
# interface), Gmail (with an app password), etc. — set these four env vars
# and password reset + deadline reminder emails start actually sending
# instead of only being logged.
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", SMTP_USER or "wink@utep.edu")
EMAIL_CONFIGURED = bool(SMTP_HOST and SMTP_USER and SMTP_PASS)
# Shared secret that an external scheduler (Render cron job, GitHub Action,
# etc.) must pass to trigger deadline reminder emails, so the endpoint can't
# be used by a random visitor to spam every student.
CRON_SECRET = os.environ.get("CRON_SECRET", "")

# ── Cost controls ────────────────────────────────────────────
# Model: Haiku is ~3x cheaper than Sonnet on both input and output tokens.
# Override with the WINK_MODEL env var if you want to trade cost for quality.
CHAT_MODEL = os.environ.get("WINK_MODEL", "claude-haiku-4-5-20251001")
CHAT_MAX_TOKENS = 1024
# Hard cap on how many characters of document text get sent to the model per
# question, regardless of how many documents the student has uploaded. This
# is the single biggest cost lever: every uploaded page otherwise gets resent
# on every single question, with no caching, for the life of the conversation.
# build_doc_context() divides this budget evenly across every uploaded
# document instead of first-come-first-served, so a slightly bigger pool
# means each individual document still gets a workable amount of its own
# content rather than being squeezed to almost nothing.
MAX_DOC_CONTEXT_CHARS = 40000
# Separate budget for admin-uploaded "general" reference documents that apply
# to every student (see build_global_doc_context()) — kept apart from the
# per-student budget above so the two caches don't invalidate each other.
MAX_GLOBAL_DOC_CONTEXT_CHARS = 20000
# Deadline extraction runs ONCE per upload, not on every question — it should
# NOT share the tight per-message chat budget above. Match extract_text()'s
# own 60,000-char storage cap instead, so extraction sees everything that was
# actually kept from the document.
DEADLINE_EXTRACTION_MAX_CHARS = 60000
# Cap for a "temporary, this-conversation-only" upload (see /upload's
# `temporary` flag). These are never written to the documents table or
# counted against MAX_DOCS_PER_STUDENT — the extracted text is handed back
# to the client, which resends it with each /chat call in that conversation
# only. Kept modest since it's on top of the student's regular doc context.
MAX_TEMP_DOC_CHARS = 20000
# How many prior chat messages (user+assistant turns) to actually send with
# each request. Conversation history otherwise grows unbounded and gets
# re-billed as input tokens on every new question.
MAX_CHAT_HISTORY_MESSAGES = 12
# Cap how many web searches Claude can run per question (each search is
# $0.01 regardless of whether Claude uses the results).
WEB_SEARCH_MAX_USES = 3
# Reject absurdly long single messages instead of billing (and paying) for
# whatever a script or a copy-pasted textbook chapter throws at the endpoint.
MAX_USER_MESSAGE_CHARS = 6000

# ── Document/global-doc cache ────────────────────────────────
# get_global_docs() is queried on every single /chat request (every student,
# every question). At "hundreds of students across many schools" scale, that's
# a lot of repeat, identical, read-only queries for data that only changes
# when an admin uploads/removes a reference document — a good fit for a
# short-lived cache instead of hitting Postgres every time. See
# services/documents.py for the cache itself.
GLOBAL_DOCS_CACHE_TTL_SECONDS = int(os.environ.get("GLOBAL_DOCS_CACHE_TTL_SECONDS", "60"))
# Same idea, for a single student's own uploaded documents — get_docs() is
# queried on every single chat message (to rebuild the document context
# sent to the model), not just page loads, making it the highest-frequency
# query in the app. Short TTL since a student may upload/delete mid-session
# and expects their next question to see it — invalidated immediately on
# that same worker by upload/delete anyway (see services/documents.py).
STUDENT_DOCS_CACHE_TTL_SECONDS = int(os.environ.get("STUDENT_DOCS_CACHE_TTL_SECONDS", "20"))

# ── DB pool sizing ───────────────────────────────────────────
# Configurable rather than hardcoded so pool size can be tuned to the actual
# deployment (worker count × threads-per-worker, expected concurrent
# students) without a code change. See extensions.py.
DB_POOL_MIN = int(os.environ.get("DB_POOL_MIN", "1"))
DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "20"))

# ── Static asset caching ──────────────────────────────────────
# How long a browser may cache files under /static/ (nav.css,
# csrf-fetch.js, images) before re-checking with the server. See the
# after_request hook in wink/__init__.py for where this is applied. Kept
# to a day by default rather than a year, since these files don't yet
# have a cache-busting/content-hash naming scheme — a shorter window
# means an edit to a shared file reaches every browser within a day
# instead of staying stuck behind a long cache on whoever already
# fetched the old version.
STATIC_CACHE_MAX_AGE_SECONDS = int(os.environ.get("STATIC_CACHE_MAX_AGE_SECONDS", str(60 * 60 * 24)))

# ── Retrieval ─────────────────────────────────────────────────
# See services/retrieval.py for the full explanation. Chunk size and
# overlap are in characters, matching every other size-related constant in
# this file. RETRIEVAL_TOP_N_* controls how many chunks get pulled in once
# a student's (or a university's reference) material is too large to fit
# under the doc-context budget in full.
RETRIEVAL_CHUNK_CHARS = 1000
RETRIEVAL_CHUNK_OVERLAP_CHARS = 150
RETRIEVAL_TOP_N_STUDENT_DOCS = 25
RETRIEVAL_TOP_N_GLOBAL_DOCS = 12

# ── Neural embeddings (optional — see services/retrieval.py) ────
# If VOYAGE_API_KEY is set and the voyageai package is installed,
# retrieval uses real semantic embeddings instead of TF-IDF's literal
# word-matching. Voyage AI is Anthropic's own recommended embeddings
# partner. Falls back to TF-IDF automatically if either isn't present —
# same graceful-degradation pattern as OCR in services/documents.py.
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")
# voyage-4-lite: confirmed directly against Voyage's current pricing page
# (docs.voyageai.com/docs/pricing) — $0.02 per 1M tokens, AND the first 200
# million tokens per account are free. voyage-3.5-lite costs the same per
# token but is listed under "older models," which get no free allowance at
# all — there's no reason to default to the model that costs strictly more
# in practice for identical per-token pricing and quality.
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "voyage-4-lite")

CLASSIFICATIONS = ["Freshman", "Sophomore", "Junior", "Senior", "Graduate", "Faculty"]
MAJORS = [
    "Accounting", "Biology", "Business Administration", "Chemistry",
    "Civil Engineering", "Communication", "Computer Science",
    "Criminal Justice", "Economics", "Education", "Electrical Engineering",
    "English", "Environmental Science", "Finance", "History",
    "Industrial Engineering", "Information Systems", "Kinesiology",
    "Management", "Marketing", "Mathematics", "Mechanical Engineering",
    "Nursing", "Political Science", "Psychology", "Public Health",
    "Social Work", "Sociology", "Spanish", "Other"
]

# A student's preferred reply language, set via /update-profile. "" (the
# default) means auto-detect — WINK replies in whatever language the
# student writes their message in, message by message, which needs no
# stored preference at all and is the right default for most students.
# Setting one of these explicitly means "always reply in this language,
# regardless of what language I type in" — useful for a student who wants
# to ask questions in English but always get answers in Spanish (or vice
# versa), which auto-detect alone can't do. Kept short and curated (like
# CLASSIFICATIONS/MAJORS above) rather than a free-text field, both to
# validate against and because a fixed list is what the dashboard's
# eventual language dropdown would offer anyway.
PREFERRED_LANGUAGES = ["", "English", "Spanish"]

# See extensions.py's documents.doc_type column comment. 'material' is the
# default for every existing upload path (backward compatible — nothing
# about a normal upload changes unless the caller explicitly sends
# doc_type=assessment).
DOC_TYPES = ["material", "assessment"]

# Content budgets for generate_practice_questions() — separate from the
# chat context budgets above since this is a one-off generation call, not
# something re-sent on every message.
PRACTICE_MATERIAL_MAX_CHARS = 30000
PRACTICE_ASSESSMENT_MAX_CHARS = 8000

# ── File-signature validation ────────────────────────────
# Extension checking alone (ALLOWED_EXT) only looks at the filename — a
# renamed executable or script can carry any extension it likes. This checks
# the actual leading bytes of the file against the signature real files of
# that type start with, so a mismatch (e.g. a .pdf that isn't really a PDF)
# gets rejected before it's ever saved or parsed.
FILE_SIGNATURES = {
    "pdf":  [b"%PDF-"],
    # docx/pptx/xlsx are all ZIP containers (OOXML) — same signature family
    "docx": [b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"],
    "pptx": [b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"],
    "xlsx": [b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"],
    "png":  [b"\x89PNG\r\n\x1a\n"],
    "jpg":  [b"\xff\xd8\xff"],
    "jpeg": [b"\xff\xd8\xff"],
    # txt has no reliable magic number — any byte sequence is valid plain text
}

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    # A hardcoded fallback secret key lets anyone forge session cookies
    # (including an "is admin" session) since Flask signs sessions with this
    # value. Generate a random one instead so a missing env var fails safe —
    # sessions just won't survive a restart until SECRET_KEY is actually set.
    SECRET_KEY = secrets.token_hex(32)
    print("WARNING: SECRET_KEY not set — using a random per-process key. "
          "Sessions will be invalidated on every restart until you set "
          "SECRET_KEY in the environment.")
