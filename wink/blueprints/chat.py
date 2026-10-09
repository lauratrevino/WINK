import json
import re
import secrets
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
from flask import (Blueprint, current_app, g, jsonify, render_template,
                    request, stream_with_context, url_for)
from werkzeug.utils import secure_filename

from .. import config
from ..errors import log_error
from ..extensions import anthropic_client, csrf, generate_csrf_token, db_cursor, release_db
from ..security import login_required, page_login_required, rate_limited, verified_required
from ..services.analytics import log_event, log_token_usage, parse_conversation_messages
from ..services.deadlines import build_deadlines_context
from ..services.documents import (build_doc_context, build_global_doc_context, get_docs,
                                   get_global_doc_names)
from ..services.retrieval import embed_texts
from ..services.practice import (generate_practice_questions, generate_practice_summary,
                                  generate_study_plan, get_due_questions, grade_quiz_answer,
                                  record_attempt, store_practice_questions)
from ..services.research import log_answer, record_student_feedback
from ..services.cron import cron_job
from ..services.system_prompt import build_chat_instructions
from ..services.campus_resources import get_resources_context_block
from ..timeutil import utcnow_naive, resolve_student_timezone, build_date_reference_block

bp = Blueprint("chat", __name__)

# Mirrors the citation-highlighting regex in templates/chat.html (kept in
# sync deliberately — this is what decides whether a filename the model
# wrote actually corresponds to a real document, so it needs to recognize
# the same things the frontend highlights as a citation in the first place).
_CITATION_FILENAME_RE = re.compile(r"\b(\S+\.(?:docx|pdf|pptx|xlsx|txt|csv|md|rtf|png|jpe?g|gif|webp|bmp|tiff?|heic|heif|mp4|mov|m4v|webm|avi|mkv))\b", re.IGNORECASE)


def _extract_citation_filenames(text):
    r"""Finds filename-looking tokens in text, excluding anything with a
    path separator in it — \S+ alone is greedy and URL/path-unaware, so
    without this a link like 'https://example.edu/handouts/notes.pdf' (or
    even a bare local path like '/uploads/notes.pdf') gets captured whole
    or partially rather than recognized as not-a-plain-filename and
    skipped. Kept as a small shared helper (rather than inlined in
    _find_unverified_citations) since it mirrors the equivalent exclusion
    in templates/chat.html's citation-highlighting regex — the two are
    meant to agree on what counts as a citation."""
    return [m for m in _CITATION_FILENAME_RE.findall(text or "") if "/" not in m]


def _find_unverified_citations(answer_text, known_filenames):
    """Filenames the model named in its answer that don't match any
    document actually shown to it this turn. This does NOT verify
    passage-level grounding (that a cited file's specific retrieved
    excerpt actually supports the claim next to it) — only the more basic
    question of whether the named file exists among what the model was
    given at all, as opposed to a name it invented. See migration
    9d4b7f2a1c88 and templates/chat.html's citation-highlighting comment
    for the fuller reasoning."""
    mentioned = set(_extract_citation_filenames(answer_text))
    if not mentioned:
        return []
    known_lower = {f.lower() for f in known_filenames}
    return sorted(name for name in mentioned if name.lower() not in known_lower)


# Demo mode is public and requires no verification — a single IP can start
# up to 5 demo sessions/hour (see demo.py), and without a tight cap on
# every AI-consuming action a demo session could otherwise sustain the same
# usage as a real verified student for hours, entirely unauthenticated.
# This caps TOTAL AI-consuming actions per demo session — chat messages,
# practice/study-plan generations, all drawing from one shared budget —
# rather than giving each endpoint its own separate allowance, since a
# separate allowance per endpoint doesn't actually bound total cost
# exposure (a demo user could still rack up 25 chats *and* several rounds
# of practice generation *and* study plans, each accepted on its own
# terms). This is what actually bounds cost from an unauthenticated,
# public feature.
_DEMO_AI_BUDGET_MAX_CALLS = 25
_DEMO_AI_BUDGET_WINDOW_SECONDS = 3600  # matches the 1-hour demo session TTL


# Safety net behind the system prompt's no-apology rule: strips opening
# sentences that apologize, concede ("You're absolutely right"), or narrate
# WINK's own process ("Let me take a look..."). Only the OPENING is
# touched; the real answer after it is left exactly as written.
_OPENER_RE = re.compile(
    r"^\s*(?:(?:you(?:'|\u2019)re (?:absolutely |totally |completely )?right|you are (?:absolutely )?right"
    r"|good catch|great catch|my apologies|i apologi[sz]e|(?:i(?:'|\u2019)m |i am )?sorry(?: for| about|,)"
    r"|my (?:mistake|bad)|i (?:missed|overlooked|should have|didn(?:'|\u2019)t read|did not read)"
    r"|let me (?:take (?:a|another) look|look|check|re-?read|re-?check|review|go back)"
    r"|looking (?:at|again|back)|thanks for (?:catching|pointing))"
    r".*?(?:[.!?]+|\u2014|:)(?=\s|$|[\U0001F300-\U0001FAFF\u2600-\u27BF])"
    r"[ \t]*(?:[\U0001F300-\U0001FAFF\u2600-\u27BF]\ufe0f?[ \t]*)*)+",
    re.IGNORECASE,
)


_OPENER_STARTS = ("you're", "you\u2019re", "you are", "good catch", "great catch", "my apolog", "i apolog",
                  "i'm sorry", "i\u2019m sorry", "i am sorry", "sorry", "my mistake", "my bad", "i missed",
                  "i overlooked", "i should have", "i didn", "i did not", "let me", "looking at", "looking again",
                  "looking back", "thanks for catching", "thanks for pointing")


def _could_be_opener(held):
    """True while the reply so far could still turn into an apology or
    narration opener, so it's worth holding back a moment longer. Anything
    else streams immediately, keeping replies instant."""
    h = held.lstrip().lower()
    if not h:
        return True
    return any(s.startswith(h) or h.startswith(s) for s in _OPENER_STARTS)


def _strip_apology_opener(text):
    cleaned = _OPENER_RE.sub("", text, count=1)
    return cleaned.lstrip() if cleaned != text else text


def _check_demo_ai_budget(s):
    """Returns a Flask response tuple to short-circuit the caller with if a
    demo account has hit its shared AI-usage budget; returns None (nothing
    to do) for a real account, or a demo account still within budget."""
    if not s.get("is_demo"):
        return None
    demo_wait = rate_limited(f"demo-ai-total:{s['id']}", max_calls=_DEMO_AI_BUDGET_MAX_CALLS,
                              window_seconds=_DEMO_AI_BUDGET_WINDOW_SECONDS)
    if demo_wait:
        return jsonify({
            "error": "You've reached the usage limit for this demo session. "
                     "Create a free account to keep using WINK!",
        }), 429
    return None


@bp.route("/chat-page")
@page_login_required
def chat_page():
    try:
        s = g.student
        log_event(s["id"], "page_view", {"page": "chat"})
        return render_template("chat.html", s=s, admin_email=config.ADMIN_EMAIL, active="chat")
    except Exception as e:
        log_error("chat.chat_page", e)
        return f"<h2>Something went wrong</h2><p>Please try again, or <form method='POST' action='/logout' style='display:inline'><input type='hidden' name='csrf_token' value='{generate_csrf_token()}'><button type='submit' style='background:none;border:none;padding:0;color:#0645AD;text-decoration:underline;cursor:pointer;font:inherit;'>log out</button></form> and back in.</p>", 500


@bp.route("/practice-page")
@page_login_required
def practice_page():
    try:
        s = g.student
        docs = get_docs(s["id"])
        known_courses = sorted({(d.get("course") or "").strip() for d in docs if (d.get("course") or "").strip()})
        log_event(s["id"], "page_view", {"page": "practice"})
        return render_template("practice.html", s=s, admin_email=config.ADMIN_EMAIL,
                               active="practice", known_courses=known_courses)
    except Exception as e:
        log_error("chat.practice_page", e)
        return f"<h2>Something went wrong</h2><p>Please try again, or <form method='POST' action='/logout' style='display:inline'><input type='hidden' name='csrf_token' value='{generate_csrf_token()}'><button type='submit' style='background:none;border:none;padding:0;color:#0645AD;text-decoration:underline;cursor:pointer;font:inherit;'>log out</button></form> and back in.</p>", 500


@bp.route("/chat", methods=["POST"])
@login_required
@verified_required
def chat():
    try:
        s = g.student
        if not config.ANTHROPIC_API_KEY:
            return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500
        demo_blocked = _check_demo_ai_budget(s)
        if demo_blocked:
            return demo_blocked
        wait = rate_limited(f"chat:{s['id']}", max_calls=60, window_seconds=60)  # anti-bot ceiling only; no human can hit 1/sec
        if wait:
            return jsonify({
                "error": "You're asking questions faster than I can keep up — please wait a moment and try again.",
                "retry_after": wait
            }), 429
        data = request.get_json() or {}
        messages = data.get("messages", [])
        # A client sending garbage here is a bad request (400), not a
        # server error (500) — 500s are what error-tracking/alerting
        # treats as "something is actually broken," which malformed
        # input isn't.
        if not isinstance(messages, list):
            return jsonify({"error": "Invalid request format."}), 400
        # Validate the WHOLE history in one pass — every message's role,
        # content type, and individual size, the last one included — so
        # nothing is checked twice and nothing earlier in the history
        # slips through unchecked.
        # Structure is validated for every message, but LENGTH is only a hard
        # error for the student's new message. Older history is trimmed to
        # fit instead of rejected: rejecting it used to break every
        # conversation after 6 questions ("Too many messages in history")
        # or after one long report (a single reply over the per-message cap).
        for m in messages:
            if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
                return jsonify({"error": "Invalid message format."}), 400
            if not isinstance(m.get("content"), str):
                return jsonify({"error": "Invalid message format."}), 400
        if not messages or messages[-1]["role"] != "user":
            return jsonify({"error": "Invalid message format."}), 400
        if len(messages[-1]["content"]) > config.MAX_USER_MESSAGE_CHARS:
            return jsonify({"error": f"That message is too long (max {config.MAX_USER_MESSAGE_CHARS} characters). Try splitting it up or attaching it as a file."}), 400
        trimmed = []
        total_chars = 0
        for m in reversed(messages[-config.MAX_CHAT_HISTORY_MESSAGES:]):
            content = m["content"]
            if len(content) > config.MAX_HISTORY_MESSAGE_CHARS:
                content = content[:config.MAX_HISTORY_MESSAGE_CHARS] + "\n\n[...earlier content trimmed...]"
            if trimmed and total_chars + len(content) > config.MAX_CHAT_HISTORY_TOTAL_CHARS:
                break
            trimmed.append({"role": m["role"], "content": content})
            total_chars += len(content)
        messages = list(reversed(trimmed))
        user_msg = messages[-1]["content"] if messages else ""
        log_event(s["id"], "question_asked", {"q": user_msg[:200]})

        conv_id = data.get("conversation_id")
        conv_row = None
        if config.DB_URL:
            with db_cursor(commit=True) as cur:
                if conv_id:
                    cur.execute("SELECT id, title, messages FROM conversations WHERE id=%s AND student_id=%s AND deleted_at IS NULL",
                                (conv_id, s["id"]))
                    conv_row = cur.fetchone()
                if not conv_row:
                    title = (str(user_msg).strip()[:60] or "New conversation")
                    cur.execute("""INSERT INTO conversations(student_id, title, messages)
                                   VALUES(%s,%s,'[]') RETURNING id, title, messages""", (s["id"], title))
                    conv_row = cur.fetchone()
            conv_id = conv_row["id"]

        messages = messages[-config.MAX_CHAT_HISTORY_MESSAGES:]
        while messages and messages[0].get("role") != "user":
            messages.pop(0)

        docs = get_docs(s["id"])
        total_doc_chars = sum(len((d.get("content") or "")) for d in docs)
        used_retrieval = total_doc_chars > config.MAX_DOC_CONTEXT_CHARS
        retrieval_backend = ("neural" if config.VOYAGE_API_KEY else "tfidf") if used_retrieval else "full_context"
        student_university = (s.get("university") or "").strip()

        # These three each do their own DB round-trip(s) — a conversation's
        # documents, its deadlines, and the global reference material.
        # This was briefly changed to run them concurrently on separate
        # threads (each pushing its own app context to get its own DB
        # connection, since psycopg2 connections aren't safe to share
        # across threads) to shave pre-stream latency. That traded away
        # something more important than it saved: a single request went
        # from using ONE pooled connection during pre-stream work (reused
        # serially via flask.g, same as everywhere else in this file) to
        # THREE simultaneously. See test_concurrency_real_db.py's small-
        # pool test — deliberately written to catch exactly this
        # failure mode (many concurrent requests against a constrained
        # pool) — which started failing with "connection pool exhausted"
        # under this change, immediately after the retrieval-bounding fix
        # above made these queries real indexed lookups instead of no-ops.
        # Sequential is the correct trade here: each of these queries is a
        # single indexed lookup, not a slow scan, so the serial cost is
        # small — nowhere near worth tripling peak per-request connection
        # usage under concurrent load.
        now = datetime.now(ZoneInfo(resolve_student_timezone(s)))
        # Shared, memoized query-embedding getter: build_doc_context()
        # (student's own documents) and build_global_doc_context()
        # (general reference material) both independently rank their
        # chunks against this SAME question text when neural retrieval
        # is active — without sharing this, that was two separate live
        # Voyage API round trips per chat message for an identical
        # embedding input. This computes it at most once, on whichever
        # of the two calls below actually needs it first (or never, if
        # neither ends up doing neural ranking — e.g. everything fits
        # in full-context mode).
        # Retrieval looks at the previous question too, so a follow-up like
        # "you missed part of it" still pulls up the assignment it's about.
        _prev_user = next((m["content"] for m in reversed(messages[:-1]) if m.get("role") == "user"), "")
        retrieval_q = (user_msg + ("\n" + _prev_user[:1000] if _prev_user else ""))
        _query_embeddings_cache = {}
        def _get_query_embeddings():
            if "v" not in _query_embeddings_cache:
                _query_embeddings_cache["v"] = (
                    embed_texts([retrieval_q], input_type="query") if config.VOYAGE_API_KEY else None
                )
            return _query_embeddings_cache["v"]
        doc_ctx = build_doc_context(docs, question=retrieval_q, sid=s["id"], get_query_embeddings=_get_query_embeddings)
        deadline_ctx = build_deadlines_context(s["id"], now=now)
        # build_global_doc_context() now decides internally (via a cheap
        # aggregate query) whether it needs full document content at all —
        # see its docstring in services/documents.py. get_global_doc_names()
        # is a separate, deliberately cheap (no content) lookup purely for
        # citation verification below, so that check doesn't force a full
        # fetch either.
        global_ctx = build_global_doc_context(student_university, question=retrieval_q, get_query_embeddings=_get_query_embeddings)

        # Every filename actually shown to the model this turn — a
        # citation naming anything outside this set (see
        # _find_unverified_citations below) named a document it was never
        # given, rather than one it just happened to summarize instead of
        # quote from.
        known_filenames = {d["orig_name"] for d in docs if d.get("orig_name")}
        known_filenames |= set(get_global_doc_names(student_university or None))

        temp_doc = data.get("temp_doc")
        if isinstance(temp_doc, dict) and temp_doc.get("content"):
            t_name = str(temp_doc.get("name") or "attached file")[:200]
            t_content = str(temp_doc["content"])[:config.MAX_TEMP_DOC_CHARS]
            temp_doc_ctx = (
                f"\n\nThe student has temporarily attached a file for THIS CONVERSATION "
                f"ONLY (not saved to their account, not one of their uploaded documents): "
                f"'{t_name}'.\n\n{t_content}"
            )
        else:
            temp_doc_ctx = ""

        # Enforce the combined ceiling. Trim least-specific material
        # first: global reference material, then the student's saved
        # documents. The file attached to THIS conversation is trimmed
        # last of all: it's what the student is asking about right now
        # (e.g. assignment instructions), and trimming its end first used
        # to silently cut off later numbered instructions.
        combined_len = len(doc_ctx) + len(global_ctx) + len(temp_doc_ctx)
        if combined_len > config.MAX_TOTAL_CONTEXT_CHARS:
            over_by = combined_len - config.MAX_TOTAL_CONTEXT_CHARS
            trim_from_global = min(len(global_ctx), over_by)
            global_ctx = global_ctx[:len(global_ctx) - trim_from_global]
            over_by -= trim_from_global
            if over_by > 0:
                trim_from_docs = min(len(doc_ctx), over_by)
                doc_ctx = doc_ctx[:len(doc_ctx) - trim_from_docs]
                if trim_from_docs:
                    doc_ctx += "\n[Some of the student's saved documents were shortened to fit.]\n"
                over_by -= trim_from_docs
            if over_by > 0:
                temp_doc_ctx = temp_doc_ctx[:max(0, len(temp_doc_ctx) - over_by)]

        today = now.strftime("%A, %B %d, %Y")
        date_reference = build_date_reference_block(now)
        # "Other" is a real, selectable option in the registration dropdown
        # (see universities_list.py) for students whose school isn't listed.
        # Newer accounts capture what "Other" actually means in
        # university_other_name (see auth.py's register()); older accounts
        # created before that field existed have it NULL. Passed straight
        # through as the literal string "Other" with nothing more specific,
        # that produced answers like "let me search for Other's campus
        # map," since the AI has no way to know "Other" isn't an actual
        # institution name — so fall back to "their university" only when
        # there's truly nothing more specific on file.
        if student_university and student_university.strip().lower() == "other":
            university_display = (s.get("university_other_name") or "").strip() or "their university"
        else:
            university_display = student_university or "their university"
        is_utep = "utep" in student_university.lower() or "el paso" in student_university.lower()
        cached_resources_block = get_resources_context_block(student_university)
        instructions = build_chat_instructions(
            s, today, university_display, is_utep, temp_doc_ctx, cached_resources_block,
        )
        system = [
            {"type": "text", "text": instructions},
            {"type": "text", "text": date_reference},
            {"type": "text", "text": deadline_ctx, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": global_ctx, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": doc_ctx, "cache_control": {"type": "ephemeral"}},
        ]
        if temp_doc_ctx:
            system.append({"type": "text", "text": temp_doc_ctx, "cache_control": {"type": "ephemeral"}})
        client = anthropic_client
        if client is None:
            return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

        student_id = s["id"]
        start_time = time.time()

        # The model's input + max_tokens must fit its 200k-token context
        # window. Estimate input generously (~3 chars/token) and leave
        # room for web search results added mid-answer, so a long
        # conversation with big documents lowers the output ceiling
        # instead of failing outright.
        _est_input_tokens = (sum(len(b.get("text", "")) for b in system) +
                             sum(len(m.get("content", "")) for m in messages)) // 3
        _ctx_window = 200000 if "haiku" in config.CHAT_MODEL else 1000000
        reply_max_tokens = max(8000, min(config.CHAT_MAX_TOKENS, _ctx_window - _est_input_tokens - 40000))

        def generate():
            full_reply = []
            usage = None
            # Default so a failure before the stream body runs (e.g. the
            # initial API call itself raising — auth error, outage) still
            # leaves this defined; it's referenced unconditionally in
            # log_answer() below regardless of how generate() exits.
            web_search_provenance = ""
            try:
                with client.messages.stream(
                    model=config.CHAT_MODEL,
                    max_tokens=reply_max_tokens,
                    system=system,
                    messages=messages,
                    tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": config.WEB_SEARCH_MAX_USES}]
                ) as stream:
                    # Real, live, word-by-word streaming — but ONLY for a
                    # response that never touches web_search. When the model
                    # searches, Anthropic returns its own narration text
                    # ("Let me search for that...", "The search results
                    # don't show...") as real interleaved text blocks around
                    # the tool calls — streaming that live would show the
                    # student WINK's search process instead of a finished
                    # answer, no matter what the system prompt says not to
                    # do (a transport problem, not a wording one). The
                    # ordinary case — a course/document question with no
                    # search — is the common one and gets full live
                    # streaming; a search-triggered response falls back to
                    # the previous drain-then-filter behavior below, exactly
                    # as before.
                    #
                    # The decision is made ONCE, from the very FIRST content
                    # block of the response: if it's a tool block, this turn
                    # is treated as search-triggered for its entire duration;
                    # if it's plain text, every text delta streams live as it
                    # arrives. This relies on Claude reliably deciding to
                    # search (emitting a tool_use/server_tool_use block)
                    # BEFORE any narration text, rather than narrating in
                    # prose first — true in practice, and reinforced by the
                    # system prompt's existing "no narrating your own
                    # process" instruction. It is not a hard guarantee: a
                    # response that starts with plain text and only decides
                    # to search partway through would still leak that
                    # opening text live. Accepted tradeoff for the large,
                    # common-case latency win — see chat speed notes.
                    stream_live = None
                    raw_text_accum = []
                    opener_buf, opener_done = [], False
                    for event in stream:
                        etype = getattr(event, "type", None)
                        if etype == "content_block_start" and stream_live is None:
                            block_type = getattr(event.content_block, "type", None)
                            # Sonnet 5 may think first; decide from the first
                            # REAL block (text vs. search), not the thinking one.
                            if block_type not in ("thinking", "redacted_thinking"):
                                stream_live = block_type not in ("server_tool_use", "web_search_tool_result", "tool_use")
                        elif etype == "text":
                            raw_text_accum.append(event.text)
                            if stream_live:
                                # Hold back the first ~300 characters so an
                                # apology/narration opener can be removed
                                # before the student ever sees it.
                                if opener_done:
                                    full_reply.append(event.text)
                                    yield event.text
                                else:
                                    opener_buf.append(event.text)
                                    held = "".join(opener_buf)
                                    if (not _could_be_opener(held) or len(held) >= 300
                                            or "\n\n" in held[40:]):
                                        opener_done = True
                                        out = _strip_apology_opener(held)
                                        full_reply.append(out)
                                        yield out
                    if stream_live and not opener_done and opener_buf:
                        out = _strip_apology_opener("".join(opener_buf))
                        full_reply.append(out)
                        yield out
                    raw_text = "".join(raw_text_accum)
                    final_answer = ""
                    try:
                        final_message = stream.get_final_message()
                        usage = final_message.usage
                        all_blocks = getattr(final_message, "content", []) or []
                        # Find the last tool-related block, then keep only the
                        # text block(s) after it — that's the real, finished
                        # answer. If the model never searched at all, there's
                        # no tool block, and every text block is kept (the
                        # ordinary, no-search case is unaffected by this).
                        last_tool_idx = -1
                        for i, block in enumerate(all_blocks):
                            if getattr(block, "type", None) in ("server_tool_use", "web_search_tool_result"):
                                last_tool_idx = i
                        final_answer = "".join(
                            getattr(block, "text", "") or ""
                            for i, block in enumerate(all_blocks)
                            if getattr(block, "type", None) == "text" and i > last_tool_idx
                        )
                        # Captures what web_search actually returned —
                        # the queries issued and the title/URL of every
                        # result — since none of that was preserved
                        # anywhere before. Without this, a web-grounded
                        # answer's research record only says the model
                        # searched the web, with no way to reconstruct
                        # which pages it actually saw; the pages
                        # themselves can change or disappear afterward,
                        # same reasoning as retrieved_context for
                        # documents. Title/URL only, not full page
                        # content — Anthropic doesn't return the raw
                        # fetched page text via this API, only what the
                        # model was shown as search result snippets.
                        provenance_lines = []
                        for block in all_blocks:
                            if getattr(block, "type", None) == "server_tool_use" and getattr(block, "name", None) == "web_search":
                                query = (block.input or {}).get("query", "")
                                if query:
                                    provenance_lines.append(f"[web search query] {query}")
                            elif getattr(block, "type", None) == "web_search_tool_result":
                                result_content = getattr(block, "content", None)
                                if isinstance(result_content, list):
                                    for result in result_content:
                                        title = getattr(result, "title", "")
                                        url = getattr(result, "url", "")
                                        if url:
                                            provenance_lines.append(f"[web result] {title} — {url}")
                        web_search_provenance = "\n".join(provenance_lines)
                    except Exception as e:
                        log_error("chat.stream_usage", e)
                    if not stream_live:
                        # Nothing has been sent to the student yet (the
                        # model used a tool) — send the filtered final
                        # answer now, all at once, same as before this
                        # change. Prefer the filtered final_answer (it
                        # correctly excludes any pre-tool-call narration
                        # text) — but if it came back empty, either because
                        # get_final_message()/content didn't populate as
                        # expected, or isn't available at all, fall back to
                        # the raw drained text so a real reply is never
                        # silently dropped to nothing.
                        reply_text = _strip_apology_opener(final_answer or raw_text)
                        if reply_text:
                            full_reply.append(reply_text)
                            yield reply_text
            except anthropic.RateLimitError as e:
                log_error("chat.stream", e, category="AI_RATE_LIMIT")
                yield "\n\nWINK is getting a lot of questions right now. Please wait a moment and try again."
            except anthropic.APITimeoutError as e:
                log_error("chat.stream", e, category="AI_TIMEOUT")
                yield "\n\nThat took too long to answer. Please try asking again."
            except anthropic.BadRequestError as e:
                # Insufficient API credit surfaces from Anthropic as a 400
                # whose message mentions "credit" — distinguish it from an
                # actual malformed-request bug so students never see a
                # confusing generic error for an account-level problem.
                is_credit_issue = "credit" in str(e).lower()
                log_error("chat.stream", e, category="AI_CREDIT" if is_credit_issue else "AI_BAD_REQUEST")
                if is_credit_issue:
                    yield "\n\nWINK is temporarily unavailable. Your information is safe — please try again shortly."
                else:
                    yield "\n\nSomething went wrong on our end — please try asking again."
            except (anthropic.APIConnectionError, anthropic.InternalServerError) as e:
                log_error("chat.stream", e, category="AI_PROVIDER_DOWN")
                yield "\n\nWINK's AI service is temporarily unavailable. Your information is safe — please try again shortly."
            except Exception as e:
                log_error("chat.stream", e, category="AI_UNKNOWN")
                yield "\n\nSomething went wrong on our end — please try asking again."
            reply = "".join(full_reply) or "I had trouble finding an answer — please try again."
            # Deliberately NOT storing the full answer text here — it's
            # already stored in full in both the conversations table (the
            # live transcript) and answer_logs (research tracking, see
            # log_answer() below). A third full copy in the events table
            # was pure storage duplication with no distinct purpose; `len`
            # is kept in case a future analytics feature wants answer-length
            # trends without a join.
            log_event(student_id, "answer_given", {"len": len(reply)})
            message_index = None
            if config.DB_URL and conv_id:
                try:
                    # Re-read the CURRENT messages under a row lock, rather than
                    # trusting conv_row's snapshot from before the AI call —
                    # that snapshot can be stale by the time we get here (the
                    # AI call can take several seconds), and two concurrent
                    # requests against the same conversation would otherwise
                    # both read the same old list and the second write would
                    # silently erase the first's exchange. FOR UPDATE makes a
                    # second concurrent request here wait for this transaction
                    # to commit, then see this exchange already appended.
                    #
                    # get_db() (called inside db_cursor()) legitimately
                    # re-acquires a fresh connection here — release_db() was
                    # called further down, back in the outer request
                    # function, before this generator's body actually starts
                    # running during the stream (see the comment there).
                    with db_cursor(commit=True) as cur:
                        cur.execute("SELECT messages FROM conversations WHERE id=%s FOR UPDATE", (conv_id,))
                        fresh = cur.fetchone()
                        saved = parse_conversation_messages(fresh["messages"]) if fresh else []
                        if not isinstance(saved, list): saved = []
                        saved.append({"role": "user", "content": user_msg, "ts": utcnow_naive().isoformat()})
                        saved.append({"role": "assistant", "content": reply, "ts": utcnow_naive().isoformat()})
                        if len(saved) > config.MAX_STORED_MESSAGES_PER_CONVERSATION:
                            # Trim from the oldest end rather than growing forever —
                            # a single very long-running conversation over a full
                            # semester shouldn't turn into an unbounded JSON blob.
                            # The AI context window (MAX_CHAT_HISTORY_MESSAGES) is
                            # already far smaller than this, so trimming old
                            # history here doesn't change what WINK can "see" —
                            # it only bounds how much a single row can grow.
                            saved = saved[-config.MAX_STORED_MESSAGES_PER_CONVERSATION:]
                        message_index = len(saved) - 1
                        cur.execute("UPDATE conversations SET messages=%s, updated_at=NOW() WHERE id=%s",
                                    (json.dumps(saved), conv_id))
                except Exception as e:
                    log_error("chat.conversation_save", e, conversation_id=conv_id)
            log_answer(
                student_id=student_id,
                question=user_msg,
                answer_text=reply,
                conversation_id=conv_id,
                message_index=message_index,
                retrieval_backend=retrieval_backend,
                chunk_count=config.RETRIEVAL_TOP_N_STUDENT_DOCS if used_retrieval else 0,
                document_ids=[d["id"] for d in docs],
                latency_ms=int((time.time() - start_time) * 1000),
                # Verbatim snapshot of what the AI actually saw — captured
                # here rather than reconstructed later, since the source
                # documents this came from can be edited or deleted
                # afterward but this string can't change retroactively.
                retrieved_context=(doc_ctx or "") + "\n\n" + (global_ctx or "")
                             + (("\n\n" + web_search_provenance) if web_search_provenance else ""),
                unverified_citations=_find_unverified_citations(reply, known_filenames),
            )
            log_token_usage(student_id, "chat", config.CHAT_MODEL, usage)

        # Every DB read needed to prepare this request (conversation row,
        # documents, deadlines, etc.) is already done above. Release the
        # connection back to the pool now rather than letting it sit idle
        # for the whole streaming duration below — see release_db()'s
        # docstring in extensions.py for why this matters under load.
        # generate() re-acquires a fresh connection via get_db() when it
        # needs one again (saving the transcript, after streaming ends).
        release_db()

        resp = current_app.response_class(stream_with_context(generate()), mimetype="text/plain")
        resp.headers["X-Accel-Buffering"] = "no"
        resp.headers["Cache-Control"] = "no-cache"
        if conv_id:
            resp.headers["X-Conversation-Id"] = str(conv_id)
        return resp
    except Exception as e:
        log_error("chat.chat", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/generate-practice", methods=["POST"])
@login_required
@verified_required
def generate_practice():
    try:
        s = g.student
        if not config.ANTHROPIC_API_KEY:
            return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500
        demo_blocked = _check_demo_ai_budget(s)
        if demo_blocked:
            return demo_blocked
        wait = rate_limited(f"practice:{s['id']}", max_calls=60, window_seconds=60)  # anti-bot ceiling only
        if wait:
            return jsonify({
                "error": "You've generated a few sets of practice questions already — please wait a bit before making more.",
                "retry_after": wait
            }), 429

        data = request.get_json() or {}
        course = (data.get("course") or "").strip()
        count = data.get("count", 8)
        # Malformed count (a non-numeric string, None, a list, etc.) used
        # to reach int(count) unvalidated deep inside
        # generate_practice_questions() and raise there — caught by the
        # outer except-Exception below, but as a generic 500 rather than
        # the 400 a bad client input should actually get.
        try:
            count = int(count)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid question count."}), 400
        qtype = (data.get("qtype") or "review").strip()
        if qtype not in ("flashcard", "review", "quiz", "assessment_quiz", "summary"):
            return jsonify({"error": "Unrecognized question type."}), 400
        temp_material = str(data.get("temp_material") or "").strip()[:config.MAX_TEMP_DOC_CHARS]
        if not course:
            return jsonify({"error": "Please specify which course."}), 400

        # Study materials are generated ONLY from the file attached for this
        # session — never from documents already uploaded to a student's
        # course pages. `course` here is just a label for organizing the
        # generated questions (grouping in the spaced-repetition review
        # queue) — it is not used to look up or pull in stored material.
        material_text = temp_material
        if not material_text.strip():
            return jsonify({"error": "Please attach a document above to generate study "
                                      "materials from — this doesn't use documents you've "
                                      "already uploaded elsewhere in WINK."}), 400

        if qtype == "summary":
            summary = generate_practice_summary(material_text, course, student_id=s["id"])
            log_event(s["id"], "practice_summary_generated", {"course": course})
            if not summary:
                return jsonify({"error": "Couldn't generate a summary from that material — please try again."}), 500
            return jsonify({"summary": summary})

        questions = generate_practice_questions(material_text, count=count, qtype=qtype, student_id=s["id"])
        questions = store_practice_questions(s["id"], course, questions, qtype=qtype, tz=resolve_student_timezone(s))
        log_event(s["id"], "practice_questions_generated", {
            "course": course, "count": len(questions), "qtype": qtype,
            "material_chars": len(temp_material),
        })
        if not questions:
            return jsonify({"error": "Couldn't generate practice questions from that material — please try again."}), 500
        return jsonify({"questions": questions, "qtype": qtype})
    except Exception as e:
        log_error("chat.generate_practice", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/generate-study-plan", methods=["POST"])
@login_required
@verified_required
def generate_study_plan_route():
    try:
        s = g.student
        if not config.ANTHROPIC_API_KEY:
            return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500
        demo_blocked = _check_demo_ai_budget(s)
        if demo_blocked:
            return demo_blocked
        wait = rate_limited(f"study-plan:{s['id']}", max_calls=60, window_seconds=60)  # anti-bot ceiling only
        if wait:
            return jsonify({
                "error": "Please wait a bit before generating another study plan.",
                "retry_after": wait
            }), 429

        data = request.get_json() or {}
        course = (data.get("course") or "").strip()
        results = data.get("results")
        material_text = str(data.get("material") or "").strip()[:config.MAX_TEMP_DOC_CHARS]
        if not course:
            return jsonify({"error": "Please specify which course."}), 400
        if not isinstance(results, list) or not results:
            return jsonify({"error": "No quiz results to build a plan from."}), 400

        clean_results = []
        for r in results:
            if isinstance(r, dict) and isinstance(r.get("question"), str) and r.get("question").strip():
                clean_results.append({"question": r["question"][:1000], "correct": bool(r.get("correct"))})
        if not clean_results:
            return jsonify({"error": "No quiz results to build a plan from."}), 400

        # Reuses the same session material the Assessment Quiz was generated
        # from (passed from the frontend) — same reasoning as generate_practice()
        # above: never pull from documents already uploaded to a course page.
        if not material_text.strip():
            return jsonify({"error": "No material available to build a study plan from."}), 400

        plan = generate_study_plan(course, material_text, clean_results, student_id=s["id"])
        log_event(s["id"], "study_plan_generated", {"course": course, "question_count": len(clean_results)})
        if not plan:
            return jsonify({"error": "Couldn't generate a study plan — please try again."}), 500
        return jsonify({"plan": plan})
    except Exception as e:
        log_error("chat.generate_study_plan_route", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/practice-attempt", methods=["POST"])
@login_required
def practice_attempt():
    try:
        s = g.student
        data = request.get_json() or {}
        question_id = data.get("question_id")
        correct = data.get("correct")
        if question_id is None or not isinstance(correct, bool):
            return jsonify({"error": "question_id and correct (true/false) are required"}), 400
        updated = record_attempt(s["id"], question_id, correct, tz=resolve_student_timezone(s))
        if not updated:
            return jsonify({"error": "Question not found"}), 404
        return jsonify({"success": True, "question": updated})
    except Exception as e:
        log_error("chat.practice_attempt", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/practice-review")
@login_required
def practice_review():
    s = g.student
    course = request.args.get("course")
    return jsonify({"questions": get_due_questions(s["id"], course=course, tz=resolve_student_timezone(s))})


@bp.route("/grade-quiz-answer", methods=["POST"])
@login_required
def grade_quiz_answer_route():
    try:
        s = g.student
        data = request.get_json() or {}
        question_id = data.get("question_id")
        selected_index = data.get("selected_index")
        if question_id is None or not isinstance(selected_index, int):
            return jsonify({"error": "question_id and selected_index are required"}), 400
        result = grade_quiz_answer(s["id"], question_id, selected_index, tz=resolve_student_timezone(s))
        if not result:
            return jsonify({"error": "Question not found, or isn't a multiple-choice question."}), 404
        return jsonify(result)
    except Exception as e:
        log_error("chat.grade_quiz_answer_route", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/rate-answer", methods=["POST"])
@login_required
def rate_answer():
    try:
        s = g.student
        data = request.get_json() or {}
        conversation_id = data.get("conversation_id")
        message_index = data.get("message_index")
        rating = data.get("rating")
        if rating not in ("up", "down"):
            return jsonify({"error": "rating must be 'up' or 'down'"}), 400
        if conversation_id is None or message_index is None:
            return jsonify({"error": "conversation_id and message_index are required"}), 400
        log_event(s["id"], "answer_feedback", {
            "conversation_id": conversation_id, "message_index": message_index, "rating": rating,
        })
        record_student_feedback(conversation_id, message_index, rating, s["id"])
        return jsonify({"success": True})
    except Exception as e:
        log_error("chat.rate_answer", e)
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500


@bp.route("/conversations")
@login_required
def list_conversations():
    s = g.student
    if not config.DB_URL: return jsonify({"conversations": []})
    try:
        with db_cursor() as cur:
            cur.execute("""SELECT id, title, messages, updated_at FROM conversations
                           WHERE student_id=%s AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT 50""", (s["id"],))
            rows = cur.fetchall()
        out = []
        for r in rows:
            msgs = parse_conversation_messages(r["messages"])
            out.append({
                "id": r["id"], "title": r["title"],
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                "message_count": len(msgs) if isinstance(msgs, list) else 0,
            })
        return jsonify({"conversations": out})
    except Exception as e:
        log_error("chat.list_conversations", e)
        return jsonify({"error": "Something went wrong on our end."}), 500


@bp.route("/conversations/<int:conv_id>")
@login_required
def get_conversation(conv_id):
    s = g.student
    if not config.DB_URL: return jsonify({"error": "No database"}), 500
    try:
        with db_cursor() as cur:
            cur.execute("SELECT id, title, messages FROM conversations WHERE id=%s AND student_id=%s AND deleted_at IS NULL",
                        (conv_id, s["id"]))
            row = cur.fetchone()
        if not row: return jsonify({"error": "Not found"}), 404
        msgs = parse_conversation_messages(row["messages"])
        return jsonify({"id": row["id"], "title": row["title"], "messages": msgs})
    except Exception as e:
        log_error("chat.get_conversation", e)
        return jsonify({"error": "Something went wrong on our end."}), 500


@bp.route("/conversations/<int:conv_id>/delete", methods=["POST"])
@login_required
def delete_conversation(conv_id):
    s = g.student
    if not config.DB_URL: return jsonify({"error": "No database"}), 500
    try:
        with db_cursor(commit=True) as cur:
            # Soft-delete only: this hides the conversation from the student
            # immediately, but the row (and its research value) is kept for 3
            # months — see purge_deleted_conversations() below for the actual
            # hard-delete after that retention window.
            cur.execute("""UPDATE conversations SET deleted_at=NOW()
                           WHERE id=%s AND student_id=%s AND deleted_at IS NULL RETURNING id""",
                        (conv_id, s["id"]))
            row = cur.fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"success": True})
    except Exception as e:
        log_error("chat.delete_conversation", e)
        return jsonify({"error": "Something went wrong on our end."}), 500


def _conversation_transcript(title, msgs):
    lines = [f"# {title}", ""]
    for m in msgs:
        who = "You" if m.get("role") == "user" else "WINK"
        lines.append(f"**{who}:** {m.get('content','')}")
        lines.append("")
    return "\n".join(lines)


@bp.route("/export-docx", methods=["POST"])
@login_required
def export_docx():
    """"Download as Word" under a WINK answer: the answer's markdown (plus
    PNGs of any diagrams, rendered by the browser) -> a formatted .docx."""
    from flask import Response
    from ..services.docx_export import build_docx, decode_images
    try:
        data = request.get_json() or {}
        text = str(data.get("text") or "")
        if not text.strip():
            return jsonify({"error": "Nothing to export."}), 400
        if len(text) > 500000:
            return jsonify({"error": "That answer is too long to export."}), 400
        images = decode_images(data.get("images"))  # keeps order; a failed diagram becomes a placeholder
        body, filename = build_docx(_strip_apology_opener(text), images)
        log_event(g.student["id"], "answer_exported_docx", {"chars": len(text)})
        return Response(body, mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                 "Cache-Control": "no-store"})
    except Exception as e:
        log_error("chat.export_docx", e)
        return jsonify({"error": "Something went wrong creating the Word file. Please try again."}), 500


@bp.route("/export-pptx", methods=["POST"])
@login_required
def export_pptx():
    """"Download as PowerPoint" under a WINK slide-deck answer."""
    from flask import Response
    from ..services.pptx_export import build_pptx
    from ..services.docx_export import decode_images
    try:
        data = request.get_json() or {}
        text = str(data.get("text") or "")
        if not text.strip():
            return jsonify({"error": "Nothing to export."}), 400
        if len(text) > 500000:
            return jsonify({"error": "That answer is too long to export."}), 400
        images = decode_images(data.get("images"))
        body, filename = build_pptx(_strip_apology_opener(text), images)
        log_event(g.student["id"], "answer_exported_pptx", {"chars": len(text)})
        return Response(body, mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                 "Cache-Control": "no-store"})
    except Exception as e:
        log_error("chat.export_pptx", e)
        return jsonify({"error": "Something went wrong creating the PowerPoint. Please try again."}), 500


@bp.route("/conversations/<int:conv_id>/export")
@login_required
def export_conversation(conv_id):
    s = g.student
    if not config.DB_URL: return jsonify({"error": "No database"}), 500
    try:
        with db_cursor() as cur:
            cur.execute("SELECT title, messages FROM conversations WHERE id=%s AND student_id=%s AND deleted_at IS NULL",
                        (conv_id, s["id"]))
            row = cur.fetchone()
        if not row: return jsonify({"error": "Not found"}), 404
        msgs = parse_conversation_messages(row["messages"])
        transcript = _conversation_transcript(row["title"], msgs)
        resp = current_app.response_class(transcript, mimetype="text/markdown")
        safe_name = secure_filename(row["title"])[:40] or "conversation"
        resp.headers["Content-Disposition"] = f'attachment; filename="{safe_name}.md"'
        return resp
    except Exception as e:
        log_error("chat.export_conversation", e)
        return jsonify({"error": "Something went wrong on our end."}), 500


@bp.route("/conversations/<int:conv_id>/share", methods=["POST"])
@login_required
def share_conversation(conv_id):
    s = g.student
    if not config.DB_URL: return jsonify({"error": "No database"}), 500
    try:
        with db_cursor(commit=True) as cur:
            cur.execute("SELECT id, share_token FROM conversations WHERE id=%s AND student_id=%s AND deleted_at IS NULL",
                        (conv_id, s["id"]))
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "Not found"}), 404
            token = row["share_token"] or secrets.token_urlsafe(24)
            if not row["share_token"]:
                cur.execute("UPDATE conversations SET share_token=%s WHERE id=%s", (token, conv_id))
        return jsonify({"share_url": url_for("chat.view_shared_conversation", token=token, _external=True)})
    except Exception as e:
        log_error("chat.share_conversation", e)
        return jsonify({"error": "Something went wrong on our end."}), 500


@bp.route("/shared/<token>")
def view_shared_conversation(token):
    if not config.DB_URL: return "Not available.", 404
    try:
        with db_cursor() as cur:
            cur.execute("""SELECT c.title, c.messages
                           FROM conversations c JOIN students s ON s.id = c.student_id
                           WHERE c.share_token=%s AND c.deleted_at IS NULL
                           AND s.account_deleted_at IS NULL""", (token,))
            row = cur.fetchone()
        if not row: return "This shared conversation could not be found.", 404
        msgs = parse_conversation_messages(row["messages"])
        return render_template("shared_conversation.html",
                               title=row["title"] or "Conversation", messages=msgs)
    except Exception as e:
        log_error("chat.view_shared_conversation", e)
        return "Something went wrong.", 500


@bp.route("/conversations/<int:conv_id>/unshare", methods=["POST"])
@login_required
def unshare_conversation(conv_id):
    s = g.student
    if not config.DB_URL: return jsonify({"error": "No database"}), 500
    try:
        with db_cursor(commit=True) as cur:
            cur.execute("UPDATE conversations SET share_token=NULL WHERE id=%s AND student_id=%s RETURNING id",
                        (conv_id, s["id"]))
            row = cur.fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        log_error("chat.unshare_conversation", e)
        return jsonify({"error": "Something went wrong on our end."}), 500


@bp.route("/purge-deleted-conversations", methods=["POST"])
@csrf.exempt
@cron_job("purge_deleted_conversations")
def purge_deleted_conversations(run_id):
    """Hard-deletes conversations that a student soft-deleted more than 3
    months ago. Meant to be called by an external scheduler, same as
    /send-deadline-reminders — same header-based auth, same run logging
    (both centralized in services/cron.py)."""
    with db_cursor(commit=True) as cur:
        cur.execute("""DELETE FROM conversations
                       WHERE deleted_at IS NOT NULL AND deleted_at < NOW() - INTERVAL '3 months'
                       RETURNING id""")
        purged = cur.fetchall()
    return {"number_processed": len(purged), "purged": len(purged)}
