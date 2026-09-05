import logging
import os
import re

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Configurable via env var because this MUST point at a mounted Render
# Persistent Disk in production — Render's own application filesystem is
# ephemeral and gets wiped on every redeploy/restart. Falling back to a
# path under BASE_DIR (inside the app's own code directory) is fine for
# local development, but would silently lose every uploaded document on
# the next deploy if left as the default in production. See
# services/health.py's upload_storage check, which warns if this is
# still pointing at the BASE_DIR fallback while ENV=production.
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or os.path.join(BASE_DIR, "uploads")
ALLOWED_EXT = {"pdf", "docx", "txt", "pptx", "xlsx", "png", "jpg", "jpeg"}

MAX_ZIP_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  
MAX_ZIP_COMPRESSION_RATIO = 100  
MAX_ZIP_ENTRY_COUNT = 5000  
MAX_PDF_PAGES = 500  
IMAGE_EXTS_NO_OCR = {"png", "jpg", "jpeg"}

DB_URL = os.environ.get("DATABASE_URL", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Comma-separated list, e.g. "a@utep.edu, b@utep.edu" — supports more than
# one admin account. ADMIN_EMAIL (singular) is still read as a fallback so
# existing single-admin deployments keep working without changing anything.
_raw_admin_emails = os.environ.get("ADMIN_EMAILS", "").strip()
if not _raw_admin_emails:
    _raw_admin_emails = os.environ.get("ADMIN_EMAIL", "").strip()
_seen_admin_emails = set()
ADMIN_EMAILS = tuple(
    e for e in (part.strip().lower() for part in _raw_admin_emails.split(","))
    if e and not (e in _seen_admin_emails or _seen_admin_emails.add(e))
)
if not ADMIN_EMAILS:
    raise RuntimeError(
        "No admin email is configured. Set ADMIN_EMAILS (comma-separated — "
        "one or more addresses) in Render's Environment tab to the email "
        "address(es) that should have admin access, then redeploy."
    )
logger.info("ADMIN_EMAILS loaded as %r", ADMIN_EMAILS)
# The first configured admin — used only where a single display/contact
# address is needed (e.g. the mailto link on Privacy/Terms). Never used for
# authorization; every access check below goes through ADMIN_EMAILS.
ADMIN_EMAIL = ADMIN_EMAILS[0]
MAX_DOCS_PER_STUDENT = 20
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
DEBUG_SHOW_RESET_LINKS = os.environ.get("DEBUG_SHOW_RESET_LINKS", "false").lower() == "true"

SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "").strip()
FROM_EMAIL = os.environ.get("FROM_EMAIL", SMTP_USER or "wink@utep.edu").strip()
EMAIL_CONFIGURED = bool(SMTP_HOST and SMTP_USER and SMTP_PASS)
logger.info(
    "EMAIL_CONFIGURED=%s SMTP_HOST=%r SMTP_USER=%r SMTP_PORT=%r",
    EMAIL_CONFIGURED, SMTP_HOST, SMTP_USER, SMTP_PORT,
)
CRON_SECRET = os.environ.get("CRON_SECRET", "")
# Optional extra hardening for the SES bounce/complaint webhook — if set,
# incoming notifications are also checked against this specific SNS topic
# ARN, on top of the signature verification that always applies. Leave
# unset and the webhook still works, verified by signature alone.
SES_NOTIFICATION_TOPIC_ARN = os.environ.get("SES_NOTIFICATION_TOPIC_ARN", "").strip()

CHAT_MODEL = os.environ.get("WINK_MODEL", "claude-haiku-4-5-20251001")
CHAT_MAX_TOKENS = 1024
MAX_DOC_CONTEXT_CHARS = 40000
MAX_GLOBAL_DOC_CONTEXT_CHARS = 20000
# A ceiling on the COMBINED size of student documents + global reference
# material + a temporarily attached file for one message — each of the
# three above is independently capped, but nothing previously bounded
# what they add up to together (up to 80,000 chars combined before this).
# Below the sum of all three individual caps on purpose, so it actually
# does something: if the combined total would exceed this, global
# reference material and the temp attachment get trimmed first (least
# specific to the actual question), keeping the student's own uploaded
# documents intact, since that's the most directly relevant material.
MAX_TOTAL_CONTEXT_CHARS = 60000
DEADLINE_EXTRACTION_MAX_CHARS = 60000
MAX_TEMP_DOC_CHARS = 20000
# Used for "what's due today/this week" date-window comparisons (deadline
# reminders, upcoming-deadline queries). The database itself stores naive
# UTC timestamps throughout, but comparing against CURRENT_DATE without a
# timezone conversion uses the DB server's timezone (UTC on Render) — for
# hours each day, UTC's "today" is already tomorrow in Mountain Time, which
# can shift the reminder window by a day. Change this if WINK is ever used
# by students outside Mountain Time.
APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "America/Denver")
MAX_CHAT_HISTORY_MESSAGES = 12
MAX_STORED_MESSAGES_PER_CONVERSATION = 400
WEB_SEARCH_MAX_USES = 3
MAX_USER_MESSAGE_CHARS = 6000
# An independent, lower ceiling on the COMBINED size of one request's
# client-supplied chat history — deliberately NOT
# MAX_CHAT_HISTORY_MESSAGES * MAX_USER_MESSAGE_CHARS, which every message
# is already bounded by individually, making that product an unreachable
# check (see the audit note in blueprints/chat.py). This value is the
# actual, separate ceiling that check now enforces.
MAX_CHAT_HISTORY_TOTAL_CHARS = 24000


DB_POOL_MIN = int(os.environ.get("DB_POOL_MIN", "1"))
DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "20"))

STATIC_CACHE_MAX_AGE_SECONDS = int(os.environ.get("STATIC_CACHE_MAX_AGE_SECONDS", str(60 * 60 * 24)))

RETRIEVAL_CHUNK_CHARS = 1000
RETRIEVAL_CHUNK_OVERLAP_CHARS = 150
RETRIEVAL_TOP_N_STUDENT_DOCS = 25
RETRIEVAL_TOP_N_GLOBAL_DOCS = 12
# Hard ceiling on how many chunk ROWS get_student_chunks()/get_global_chunks()
# will ever pull into Python for one retrieval-triggered message, regardless
# of how many chunks actually exist for that student/university. Previously
# unbounded (see migration 7c2f19a6d3e1) — with the student document cap (20
# docs) and per-document extraction cap (~60,000 chars), that could mean
# thousands of chunks and their embeddings loaded per question. When the
# question-aware keyword pre-filter below narrows the candidate set below
# this, the cap never actually triggers; it's the backstop for when it
# doesn't (a very generic question, or no question at all).
RETRIEVAL_MAX_CANDIDATE_CHUNKS = 300

VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "voyage-4-lite")

CLASSIFICATIONS = ["Freshman", "Sophomore", "Junior", "Senior", "Graduate", "Faculty", "Other"]
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

PREFERRED_LANGUAGES = [
    "Arabic", "Chinese (Mandarin)", "English", "Filipino (Tagalog)", "French",
    "German", "Gujarati", "Haitian Creole", "Hindi", "Italian", "Japanese",
    "Korean", "Persian (Farsi)", "Polish", "Portuguese", "Punjabi", "Russian",
    "Somali", "Spanish", "Swahili", "Thai", "Turkish", "Ukrainian", "Urdu",
    "Vietnamese",
]

DOC_TYPES = ["syllabus", "course_calendar", "assignment_instructions", "notes", "slides", "handout", "assessment", "other"]

from .universities_list import UNIVERSITIES  # noqa: E402 — see that file for why this is separate

TERMS_VERSION = "2026-08-17"

PRACTICE_MATERIAL_MAX_CHARS = 30000
PRACTICE_ASSESSMENT_MAX_CHARS = 8000

FILE_SIGNATURES = {
    "pdf":  [b"%PDF-"],
    "docx": [b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"],
    "pptx": [b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"],
    "xlsx": [b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"],
    "png":  [b"\x89PNG\r\n\x1a\n"],
    "jpg":  [b"\xff\xd8\xff"],
    "jpeg": [b"\xff\xd8\xff"],
}

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY environment variable is not set. Set it in Render's "
        "Environment tab (a long random string — e.g. `python -c \"import "
        "secrets; print(secrets.token_hex(32))\"`) and redeploy. Running "
        "without a fixed SECRET_KEY means every worker process generates "
        "its own random key, which can silently invalidate sessions/CSRF "
        "tokens depending on which worker handles a given request."
    )
