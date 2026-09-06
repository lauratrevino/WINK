"""Caches contact info (phone/email/office/hours) for the handful of
common campus support offices — Financial Aid, Counseling, Advising,
Writing Center, Tutoring, Career Center — per university.

Why this exists: the chat system prompt tells WINK to always include real
contact info when it mentions a specific office, and to search for it if
it doesn't already have it (see system_prompt.py). That's the right
behavior for accuracy, but it meant a live web_search tool call on every
single chat message that touched one of these same handful of offices —
real latency, repeated for the same static-ish information, over and
over, across every student.

This module runs that same lookup (a real Anthropic call with web_search,
the same pipeline the chat itself would otherwise use live) on a
schedule instead — see refresh_all_known_resources(), meant to be called
periodically (e.g. weekly) by a cron script, same pattern as
send_deadline_reminders.sh / send_weekly_digest.sh. The chat request path
then only reads from this cache (get_resources_context_block()) and
falls back to a live search only for anything not covered here.
"""
import json
import logging

from .. import config
from ..errors import log_error
from ..extensions import anthropic_client, db_cursor
from ..timeutil import utcnow_naive

logger = logging.getLogger(__name__)

# Resource categories maintained for every university. Names lean UTEP-
# specific in a couple of cases (CASS is UTEP's accommodations/tutoring
# office) since UTEP is the pilot institution; for any other university
# the search prompt below still resolves to that school's actual
# equivalent office rather than assuming UTEP's naming applies there.
KNOWN_RESOURCE_CATEGORIES = [
    ("financial_aid", "Financial Aid Office"),
    ("counseling", "Counseling and Psychological Services"),
    ("advising", "Academic Advising"),
    ("writing_center", "University Writing Center"),
    ("tutoring", "Tutoring / Learning Center"),
    ("career_center", "Career Center"),
]


def _is_stale(refreshed_at):
    if refreshed_at is None:
        return True
    age_days = (utcnow_naive() - refreshed_at).total_seconds() / 86400
    return age_days > config.CAMPUS_RESOURCE_CACHE_MAX_AGE_DAYS


def get_resources_context_block(university):
    """Returns a formatted system-prompt block of every non-stale cached
    resource for this university, or '' if none are cached yet (e.g. a
    university that hasn't had its first refresh run, or one not in
    KNOWN_RESOURCE_CATEGORIES at all). Empty string is safe to splice
    into the prompt unconditionally — see chat.py."""
    if not config.DB_URL or not university:
        return ""
    try:
        with db_cursor() as cur:
            cur.execute(
                """SELECT resource_key, display_name, contact_info, refreshed_at
                   FROM campus_resources WHERE lower(university)=lower(%s)""",
                (university,),
            )
            rows = cur.fetchall()
    except Exception as e:
        log_error("services.campus_resources.get_resources_context_block", e)
        return ""
    fresh = [r for r in rows if not _is_stale(r["refreshed_at"]) and (r["contact_info"] or "").strip()]
    if not fresh:
        return ""
    lines = [
        "CAMPUS RESOURCES (verified contact info, already looked up — use this "
        "directly instead of searching for these specific offices; only search "
        "live if the office a student needs isn't listed here):",
    ]
    for r in fresh:
        lines.append(f"- {r['display_name']}: {r['contact_info']}")
    return "\n".join(lines)


def _upsert_resource(university, resource_key, display_name, contact_info, source_urls):
    # Writes the refreshed_at timestamp from Python (utcnow_naive()) rather
    # than SQL's NOW() — this file's staleness check (_is_stale) compares
    # against utcnow_naive() too, and mixing SQL-side NOW() with a
    # Python-side comparison risks a mismatch if the DB session isn't in
    # UTC (see auth.py's password-reset token expiry for the same
    # write-and-compare-in-Python pattern).
    with db_cursor(commit=True) as cur:
        cur.execute(
            """INSERT INTO campus_resources
               (university, resource_key, display_name, contact_info, source_urls, refreshed_at)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (university, resource_key) DO UPDATE SET
                 display_name=EXCLUDED.display_name,
                 contact_info=EXCLUDED.contact_info,
                 source_urls=EXCLUDED.source_urls,
                 refreshed_at=EXCLUDED.refreshed_at""",
            (university, resource_key, display_name, contact_info, json.dumps(source_urls), utcnow_naive()),
        )


def refresh_resource(university, resource_key, display_name):
    """Looks up ONE office's current contact info via a real Anthropic +
    web_search call and caches the result. Returns True on success, False
    if the lookup failed or turned up nothing usable (logged either way,
    never raises — a failed refresh just leaves the previous cached value,
    if any, in place until the next scheduled run)."""
    if not anthropic_client:
        return False
    try:
        resp = anthropic_client.messages.create(
            model=config.CHAT_MODEL,
            # Was 1024 — too tight once a real web_search call is involved.
            # The search results (query + returned snippets) get counted as
            # part of this same turn's output before the model ever gets to
            # write its actual JSON answer, so a small budget means the
            # response hits max_tokens and stops with NO text block at all —
            # exactly the "Expecting value: line 1 column 1 (char 0)" empty-
            # string JSON error this produced. Same failure shape, same fix,
            # as extract_deadlines() needed for the same reason.
            max_tokens=4096,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
            system=(
                "You look up current, real contact info for a specific university "
                "office. Use web_search to find the office's actual current phone "
                "number, email, physical location/room, and hours — prefer the "
                "university's own .edu pages. Respond with ONLY a JSON object, no "
                "other text, in exactly this shape: "
                '{"found": true, "contact_info": "Phone: ... | Email: ... | '
                'Location: ... | Hours: ...", "source_urls": ["https://..."]} '
                '— include only the fields you actually found (omit a piece '
                'entirely rather than guessing or leaving a placeholder). If you '
                'cannot find this office at all for this university, respond with '
                '{"found": false}.'
            ),
            messages=[{
                "role": "user",
                "content": f"University: {university}\nOffice: {display_name}",
            }],
        )
        text_blocks = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        raw = "".join(text_blocks).strip()
        if not raw:
            # Distinguish this from a malformed-JSON case below — this
            # means the model never emitted a text block at all (most
            # likely hit max_tokens mid-search, or refused for some other
            # reason). stop_reason makes that diagnosable directly from
            # logs instead of just an opaque empty-string JSON error.
            logger.warning(
                "Campus resource lookup for %s / %s returned no text at all "
                "(stop_reason=%s) — likely ran out of max_tokens mid-search.",
                university, display_name, getattr(resp, "stop_reason", None),
            )
            return False
        # Same tolerant fence-stripping as elsewhere (see json_utils) —
        # the model occasionally wraps JSON in a ```json fence despite
        # being asked not to.
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        data = json.loads(raw)
        if not data.get("found"):
            logger.info("Campus resource lookup found nothing for %s / %s", university, display_name)
            return False
        contact_info = (data.get("contact_info") or "").strip()
        if not contact_info:
            return False
        source_urls = data.get("source_urls") or []
        _upsert_resource(university, resource_key, display_name, contact_info, source_urls)
        return True
    except Exception as e:
        log_error("services.campus_resources.refresh_resource", e, university=university, resource_key=resource_key)
        return False


def refresh_all_known_resources(university):
    """Refreshes every category in KNOWN_RESOURCE_CATEGORIES for one
    university. Meant to be called by a scheduled cron script (see
    scripts/refresh_campus_resources.sh), not from a request path — each
    call is a handful of real Anthropic+web_search round trips."""
    results = {}
    for resource_key, display_name in KNOWN_RESOURCE_CATEGORIES:
        results[resource_key] = refresh_resource(university, resource_key, display_name)
    return results
