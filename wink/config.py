import logging
import os
import re

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
ALLOWED_EXT = {"pdf", "docx", "txt", "pptx", "xlsx", "png", "jpg", "jpeg"}

MAX_ZIP_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  
MAX_ZIP_COMPRESSION_RATIO = 100  
MAX_ZIP_ENTRY_COUNT = 5000  
MAX_PDF_PAGES = 500  
IMAGE_EXTS_NO_OCR = {"png", "jpg", "jpeg"}

DB_URL = os.environ.get("DATABASE_URL", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()
if not ADMIN_EMAIL:
    raise RuntimeError(
        "ADMIN_EMAIL environment variable is not set. Set it in Render's "
        "Environment tab to the email address that should have admin access, "
        "then redeploy."
    )
logger.info("ADMIN_EMAIL loaded as %r", ADMIN_EMAIL)
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

GLOBAL_DOCS_CACHE_TTL_SECONDS = int(os.environ.get("GLOBAL_DOCS_CACHE_TTL_SECONDS", "60"))
STUDENT_DOCS_CACHE_TTL_SECONDS = int(os.environ.get("STUDENT_DOCS_CACHE_TTL_SECONDS", "20"))

DB_POOL_MIN = int(os.environ.get("DB_POOL_MIN", "1"))
DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "20"))

STATIC_CACHE_MAX_AGE_SECONDS = int(os.environ.get("STATIC_CACHE_MAX_AGE_SECONDS", str(60 * 60 * 24)))

RETRIEVAL_CHUNK_CHARS = 1000
RETRIEVAL_CHUNK_OVERLAP_CHARS = 150
RETRIEVAL_TOP_N_STUDENT_DOCS = 25
RETRIEVAL_TOP_N_GLOBAL_DOCS = 12

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
