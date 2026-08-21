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
from ..extensions import anthropic_client, csrf, generate_csrf_token, get_db, release_db
from ..security import login_required, page_login_required, rate_limited, verified_required
from ..services.analytics import log_event, log_token_usage, parse_conversation_messages
from ..services.deadlines import build_deadlines_context
from ..services.documents import (build_doc_context, build_global_doc_context, get_docs,
                                   get_global_doc_names)
from ..services.practice import (generate_practice_questions, generate_practice_summary,
                                  generate_study_plan, get_due_questions, grade_quiz_answer,
                                  record_attempt, store_practice_questions)
from ..services.research import log_answer, record_student_feedback
from ..services.system_prompt import build_chat_instructions
from ..timeutil import utcnow_naive

bp = Blueprint("chat", __name__)

# Mirrors the citation-highlighting regex in templates/chat.html (kept in
# sync deliberately — this is what decides whether a filename the model
# wrote actually corresponds to a real document, so it needs to recognize
# the same things the frontend highlights as a citation in the first place).
_CITATION_FILENAME_RE = re.compile(r"\b(\S+\.(?:docx|pdf|pptx|xlsx|txt|png|jpe?g))\b", re.IGNORECASE)


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
_DEMO_AI_BUDGET_WINDOW_SECONDS = 21600  # matches the 6-hour demo session TTL


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
        wait = rate_limited(f"chat:{s['id']}", max_calls=20, window_seconds=60)
        if wait:
            return jsonify({
                "error": "You're asking questions faster than I can keep up — please wait a moment and try again.",
                "retry_after": wait
            }), 429
        data = request.get_json() or {}
        messages = data.get("messages", [])
        # Malformed input here previously fell through to the generic
        # except-Exception handler further down and returned a 500 —
        # safe (no crash, no stack trace leaked), but the wrong status
        # code: a client sending garbage is a bad request (400), not a
        # server error (500), and 500s are what error-tracking/alerting
        # typically treats as "something is actually broken here."
        if not isinstance(messages, list):
            return jsonify({"error": "Invalid request format."}), 400
        if messages and not (isinstance(messages[-1], dict) and isinstance(messages[-1].get("content"), str)):
            return jsonify({"error": "Invalid message format."}), 400
        user_msg = messages[-1]["content"] if messages else ""
        if isinstance(user_msg, str) and len(user_msg) > config.MAX_USER_MESSAGE_CHARS:
            return jsonify({"error": f"That message is too long (max {config.MAX_USER_MESSAGE_CHARS} characters). Please shorten it and try again."}), 400
        log_event(s["id"], "question_asked", {"q": user_msg[:200]})

        conv_id = data.get("conversation_id")
        conv_row = None
        if config.DB_URL:
            conn = get_db(); cur = conn.cursor()
            if conv_id:
                cur.execute("SELECT id, title, messages FROM conversations WHERE id=%s AND student_id=%s AND deleted_at IS NULL",
                            (conv_id, s["id"]))
                conv_row = cur.fetchone()
            if not conv_row:
                title = (str(user_msg).strip()[:60] or "New conversation")
                cur.execute("""INSERT INTO conversations(student_id, title, messages)
                               VALUES(%s,%s,'[]') RETURNING id, title, messages""", (s["id"], title))
                conv_row = cur.fetchone()
                conn.commit()
            cur.close()
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
        doc_ctx = build_doc_context(docs, question=user_msg, sid=s["id"])
        deadline_ctx = build_deadlines_context(s["id"])
        # build_global_doc_context() now decides internally (via a cheap
        # aggregate query) whether it needs full document content at all —
        # see its docstring in services/documents.py. get_global_doc_names()
        # is a separate, deliberately cheap (no content) lookup purely for
        # citation verification below, so that check doesn't force a full
        # fetch either.
        global_ctx = build_global_doc_context(student_university, question=user_msg)

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

        # Enforce the combined ceiling — each piece above already has its
        # own individual cap, but nothing previously bounded what they
        # add up to together. Trim least-specific-to-the-question
        # material first (global reference material, then the temp
        # attachment), keeping the student's own uploaded documents
        # intact since that's most directly relevant to their question.
        combined_len = len(doc_ctx) + len(global_ctx) + len(temp_doc_ctx)
        if combined_len > config.MAX_TOTAL_CONTEXT_CHARS:
            over_by = combined_len - config.MAX_TOTAL_CONTEXT_CHARS
            trim_from_global = min(len(global_ctx), over_by)
            global_ctx = global_ctx[:len(global_ctx) - trim_from_global]
            over_by -= trim_from_global
            if over_by > 0:
                temp_doc_ctx = temp_doc_ctx[:max(0, len(temp_doc_ctx) - over_by)]

        now = datetime.now(ZoneInfo(config.APP_TIMEZONE))
        today = now.strftime("%A, %B %d, %Y")
        university_display = student_university or "their university"
        is_utep = "utep" in student_university.lower() or "el paso" in student_university.lower()
        instructions = build_chat_instructions(
            s, today, university_display, is_utep, temp_doc_ctx,
        )
        system = [
            {"type": "text", "text": instructions},
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

        def generate():
            full_reply = []
            usage = None
            try:
                with client.messages.stream(
                    model=config.CHAT_MODEL,
                    max_tokens=config.CHAT_MAX_TOKENS,
                    system=system,
                    messages=messages,
                    tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": config.WEB_SEARCH_MAX_USES}]
                ) as stream:
                    for text in stream.text_stream:
                        full_reply.append(text)
                        yield text
                    web_search_provenance = ""
                    try:
                        final_message = stream.get_final_message()
                        usage = final_message.usage
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
                        for block in getattr(final_message, "content", []) or []:
                            if getattr(block, "type", None) == "server_tool_use" and getattr(block, "name", None) == "web_search":
                                query = (block.input or {}).get("query", "")
                                if query:
                                    provenance_lines.append(f"[web search query] {query}")
                            elif getattr(block, "type", None) == "web_search_tool_result":
                                content = getattr(block, "content", None)
                                if isinstance(content, list):
                                    for result in content:
                                        title = getattr(result, "title", "")
                                        url = getattr(result, "url", "")
                                        if url:
                                            provenance_lines.append(f"[web result] {title} — {url}")
                        web_search_provenance = "\n".join(provenance_lines)
                    except Exception as e:
                        log_error("chat.stream_usage", e)
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
                    conn = get_db(); cur = conn.cursor()
                    # Re-read the CURRENT messages under a row lock, rather than
                    # trusting conv_row's snapshot from before the AI call —
                    # that snapshot can be stale by the time we get here (the
                    # AI call can take several seconds), and two concurrent
                    # requests against the same conversation would otherwise
                    # both read the same old list and the second write would
                    # silently erase the first's exchange. FOR UPDATE makes a
                    # second concurrent request here wait for this transaction
                    # to commit, then see this exchange already appended.
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
                    conn.commit(); cur.close()
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
        wait = rate_limited(f"practice:{s['id']}", max_calls=5, window_seconds=600)
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
        questions = store_practice_questions(s["id"], course, questions, qtype=qtype)
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
        wait = rate_limited(f"study-plan:{s['id']}", max_calls=10, window_seconds=600)
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
        updated = record_attempt(s["id"], question_id, correct)
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
    return jsonify({"questions": get_due_questions(s["id"], course=course)})


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
        result = grade_quiz_answer(s["id"], question_id, selected_index)
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
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT id, title, messages, updated_at FROM conversations
                       WHERE student_id=%s AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT 50""", (s["id"],))
        rows = cur.fetchall(); cur.close()
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
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT id, title, messages FROM conversations WHERE id=%s AND student_id=%s AND deleted_at IS NULL",
                    (conv_id, s["id"]))
        row = cur.fetchone(); cur.close()
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
        conn = get_db(); cur = conn.cursor()
        # Soft-delete only: this hides the conversation from the student
        # immediately, but the row (and its research value) is kept for 3
        # months — see purge_deleted_conversations() below for the actual
        # hard-delete after that retention window.
        cur.execute("""UPDATE conversations SET deleted_at=NOW()
                       WHERE id=%s AND student_id=%s AND deleted_at IS NULL RETURNING id""",
                    (conv_id, s["id"]))
        row = cur.fetchone()
        conn.commit(); cur.close()
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


@bp.route("/conversations/<int:conv_id>/export")
@login_required
def export_conversation(conv_id):
    s = g.student
    if not config.DB_URL: return jsonify({"error": "No database"}), 500
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT title, messages FROM conversations WHERE id=%s AND student_id=%s AND deleted_at IS NULL",
                    (conv_id, s["id"]))
        row = cur.fetchone(); cur.close()
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
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT id, share_token FROM conversations WHERE id=%s AND student_id=%s AND deleted_at IS NULL",
                    (conv_id, s["id"]))
        row = cur.fetchone()
        if not row:
            cur.close()
            return jsonify({"error": "Not found"}), 404
        token = row["share_token"] or secrets.token_urlsafe(24)
        if not row["share_token"]:
            cur.execute("UPDATE conversations SET share_token=%s WHERE id=%s", (token, conv_id))
            conn.commit()
        cur.close()
        return jsonify({"share_url": url_for("chat.view_shared_conversation", token=token, _external=True)})
    except Exception as e:
        log_error("chat.share_conversation", e)
        return jsonify({"error": "Something went wrong on our end."}), 500


@bp.route("/shared/<token>")
def view_shared_conversation(token):
    if not config.DB_URL: return "Not available.", 404
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT c.title, c.messages
                       FROM conversations c JOIN students s ON s.id = c.student_id
                       WHERE c.share_token=%s AND c.deleted_at IS NULL
                       AND s.account_deleted_at IS NULL""", (token,))
        row = cur.fetchone(); cur.close()
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
        conn = get_db(); cur = conn.cursor()
        cur.execute("UPDATE conversations SET share_token=NULL WHERE id=%s AND student_id=%s RETURNING id",
                    (conv_id, s["id"]))
        row = cur.fetchone()
        conn.commit(); cur.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        log_error("chat.unshare_conversation", e)
        return jsonify({"error": "Something went wrong on our end."}), 500


@bp.route("/purge-deleted-conversations", methods=["POST"])
@csrf.exempt
def purge_deleted_conversations():
    """Hard-deletes conversations that a student soft-deleted more than 3
    months ago. Meant to be called by an external scheduler, same as
    /send-deadline-reminders — same header-based auth, same run logging."""
    provided = request.headers.get("X-WINK-Cron-Secret", "")
    if not provided:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            provided = auth_header[len("Bearer "):]
    if not config.CRON_SECRET or not secrets.compare_digest(provided, config.CRON_SECRET):
        return jsonify({"error": "Not authorized"}), 403
    if not config.DB_URL:
        return jsonify({"error": "No database"}), 500

    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT INTO cron_runs(job_name) VALUES('purge_deleted_conversations') RETURNING id")
    run_id = cur.fetchone()["id"]
    conn.commit(); cur.close()

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""DELETE FROM conversations
                       WHERE deleted_at IS NOT NULL AND deleted_at < NOW() - INTERVAL '3 months'
                       RETURNING id""")
        purged = cur.fetchall()
        conn.commit(); cur.close()

        conn = get_db(); cur = conn.cursor()
        cur.execute("UPDATE cron_runs SET completed_at=NOW(), number_processed=%s WHERE id=%s",
                    (len(purged), run_id))
        conn.commit(); cur.close()

        return jsonify({"purged": len(purged)})
    except Exception as e:
        log_error("chat.purge_deleted_conversations", e)
        try:
            conn = get_db(); cur = conn.cursor()
            cur.execute("UPDATE cron_runs SET completed_at=NOW(), last_error=%s WHERE id=%s",
                        (str(e)[:500], run_id))
            conn.commit(); cur.close()
        except Exception:
            pass
        return jsonify({"error": "Something went wrong on our end."}), 500
