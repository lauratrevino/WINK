import os, json, uuid, traceback
from flask import (Flask, render_template, request, jsonify,
                   session, redirect, url_for)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "wink-utep-2025")

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
ALLOWED_EXT   = {"pdf","docx","doc","txt","pptx","xlsx","png","jpg","jpeg"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

DB_URL            = os.environ.get("DATABASE_URL", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "lhall@utep.edu").lower()

CLASSIFICATIONS = ["Freshman","Sophomore","Junior","Senior","Graduate"]
MAJORS = [
    "Accounting","Biology","Business Administration","Chemistry",
    "Civil Engineering","Communication","Computer Science",
    "Criminal Justice","Economics","Education","Electrical Engineering",
    "English","Environmental Science","Finance","History",
    "Industrial Engineering","Information Systems","Kinesiology",
    "Management","Marketing","Mathematics","Mechanical Engineering",
    "Nursing","Political Science","Psychology","Public Health",
    "Social Work","Sociology","Spanish","Other"
]

# ── Text Extraction ───────────────────────────────────────
def extract_text(filepath, orig_name):
    ext = orig_name.rsplit(".", 1)[-1].lower() if "." in orig_name else ""
    text = ""
    try:
        if ext == "txt":
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        elif ext == "pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(filepath)
                pages = []
                for i, page in enumerate(reader.pages):
                    try:
                        t = page.extract_text()
                        if t and t.strip():
                            pages.append(f"[Page {i+1}]\n{t.strip()}")
                    except Exception as pe:
                        print(f"PDF page {i+1} error: {pe}")
                text = "\n\n".join(pages)
                print(f"PDF extracted {len(text)} chars from {len(reader.pages)} pages")
            except Exception as e:
                print(f"PDF extract failed: {e}"); traceback.print_exc()
        elif ext in ("doc", "docx"):
            try:
                from docx import Document
                doc = Document(filepath)
                parts = []
                for p in doc.paragraphs:
                    if p.text.strip():
                        parts.append(p.text.strip())
                for table in doc.tables:
                    for row in table.rows:
                        cells = [c.text.strip() for c in row.cells if c.text.strip()]
                        if cells:
                            parts.append(" | ".join(cells))
                text = "\n".join(parts)
                print(f"DOCX extracted {len(text)} chars")
            except Exception as e:
                print(f"DOCX extract failed: {e}"); traceback.print_exc()
        elif ext == "pptx":
            try:
                from pptx import Presentation
                prs = Presentation(filepath)
                slides = []
                for i, slide in enumerate(prs.slides):
                    parts = []
                    for shape in slide.shapes:
                        if hasattr(shape, "text") and shape.text.strip():
                            parts.append(shape.text.strip())
                    if parts:
                        slides.append(f"[Slide {i+1}]\n" + "\n".join(parts))
                text = "\n\n".join(slides)
                print(f"PPTX extracted {len(text)} chars")
            except Exception as e:
                print(f"PPTX extract failed: {e}"); traceback.print_exc()
        elif ext in ("jpg","jpeg","png"):
            text = f"[Image file: {orig_name}]"
        else:
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except:
                text = ""
    except Exception as e:
        print(f"extract_text error for {orig_name}: {e}"); text = ""
    if len(text) > 60000:
        text = text[:60000] + "\n\n[Document truncated at 60,000 characters]"
    return text.strip()

def build_doc_context(docs):
    if not docs:
        return "\n\nThe student has not uploaded any course documents yet."
    has_content = any((d.get("content") or "").strip() for d in docs)
    if not has_content:
        ctx = f"\n\nThe student has {len(docs)} uploaded file(s) but no text could be extracted. "
        ctx += "Files: " + ", ".join(d["orig_name"] for d in docs)
        return ctx
    ctx = f"\n\n{'='*60}\nSTUDENT'S UPLOADED COURSE DOCUMENTS ({len(docs)} files)\n"
    ctx += "Answer questions using the actual content of these documents.\n"
    ctx += "Quote specific text, deadlines, requirements directly from the documents.\n"
    ctx += f"{'='*60}\n\n"
    for i, d in enumerate(docs):
        content = (d.get("content") or "").strip()
        ctx += f"[DOCUMENT {i+1}] {d['orig_name']}\n"
        ctx += f"Course: {d['course']} | Size: {round(d.get('size_bytes',0)/1024,1)} KB\n"
        ctx += f"Content ({len(content)} chars):\n"
        ctx += content if content else "[No text could be extracted]"
        ctx += f"\n\n{'-'*40}\n\n"
    ctx += f"{'='*60}\n"
    return ctx

# ── DB ────────────────────────────────────────────────────
def get_db():
    import psycopg2
    from psycopg2.extras import RealDictCursor
    return psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)

def init_db():
    if not DB_URL:
        print("WARNING: No DATABASE_URL set.")
        return
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS students (
            id SERIAL PRIMARY KEY, email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, first_name TEXT NOT NULL,
            last_name TEXT NOT NULL, classification TEXT NOT NULL,
            major TEXT NOT NULL, created_at TIMESTAMP DEFAULT NOW())""")
        cur.execute("""CREATE TABLE IF NOT EXISTS documents (
            id SERIAL PRIMARY KEY,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            filename TEXT NOT NULL, orig_name TEXT NOT NULL,
            course TEXT NOT NULL, size_bytes INTEGER DEFAULT 0,
            content TEXT DEFAULT '',
            uploaded_at TIMESTAMP DEFAULT NOW())""")
        cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS content TEXT DEFAULT ''")
        # Use TEXT for payload — simple and reliable across all Postgres versions
        cur.execute("""CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY, student_id INTEGER,
            event_type TEXT NOT NULL,
            payload TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT NOW())""")
        conn.commit(); cur.close(); conn.close()
        print("DB initialized OK.")
    except Exception as e:
        print(f"DB init error: {e}"); traceback.print_exc()

init_db()

# ── Helpers ───────────────────────────────────────────────
def current_student():
    if "sid" not in session or not DB_URL:
        return None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM students WHERE id=%s", (session["sid"],))
        s = cur.fetchone(); cur.close(); conn.close()
        return dict(s) if s else None
    except Exception as e:
        print(f"current_student error: {e}"); return None

def log_event(sid, etype, payload=None):
    """Log every user action to the events table."""
    if not DB_URL:
        return
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "INSERT INTO events(student_id, event_type, payload) VALUES(%s, %s, %s)",
            (sid, etype, json.dumps(payload or {}))
        )
        conn.commit(); cur.close(); conn.close()
        print(f"EVENT LOGGED: {etype} for student {sid}")
    except Exception as e:
        print(f"log_event ERROR: {e}")
        traceback.print_exc()

def get_docs(sid):
    if not DB_URL: return []
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM documents WHERE student_id=%s ORDER BY uploaded_at DESC", (sid,))
        docs = [dict(r) for r in cur.fetchall()]; cur.close(); conn.close()
        return docs
    except Exception as e:
        print(f"get_docs error: {e}"); return []

def safe_payload(raw):
    """Safely parse a payload value regardless of whether it's str, dict, or None."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except:
        return {}

# ── Auth ──────────────────────────────────────────────────
@app.route("/")
def landing():
    # Always show landing page so students see the welcome screen first
    try:
        return render_template("landing.html")
    except Exception as e:
        print(f"landing error: {e}"); return render_template("landing.html")

@app.route("/register", methods=["GET","POST"])
def register():
    def err(msg):
        return render_template("register.html", error=msg,
                               classifications=CLASSIFICATIONS, majors=MAJORS)
    try:
        if request.method == "POST":
            email = request.form.get("email","").strip().lower()
            pw    = request.form.get("password","").strip()
            fn    = request.form.get("first_name","").strip()
            ln    = request.form.get("last_name","").strip()
            cl    = request.form.get("classification","").strip()
            major = request.form.get("major","").strip()
            if not all([email,pw,fn,ln,cl,major]):
                return err("All fields are required.")
            if not (email.endswith("@miners.utep.edu") or email.endswith("@utep.edu")):
                return err("Please use your UTEP email (@miners.utep.edu or @utep.edu).")
            if len(pw) < 6:
                return err("Password must be at least 6 characters.")
            if not DB_URL:
                return err("Database not configured.")
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT id FROM students WHERE email=%s", (email,))
            if cur.fetchone():
                cur.close(); conn.close()
                return err("Account already exists — please log in.")
            cur.execute("""INSERT INTO students(email,password_hash,first_name,last_name,classification,major)
                           VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (email, generate_password_hash(pw), fn, ln, cl, major))
            new_id = cur.fetchone()["id"]
            conn.commit(); cur.close(); conn.close()
            session["sid"] = new_id
            log_event(new_id, "account_created", {"email":email,"classification":cl,"major":major})
            return redirect(url_for("documents"))
        return render_template("register.html", error=None,
                               classifications=CLASSIFICATIONS, majors=MAJORS)
    except Exception as e:
        print(f"register error: {e}"); traceback.print_exc()
        return err(f"Something went wrong: {e}")

@app.route("/login", methods=["GET","POST"])
def login():
    try:
        if request.method == "POST":
            email = request.form.get("email","").strip().lower()
            pw    = request.form.get("password","").strip()
            if not DB_URL:
                return render_template("login.html", error="Database not configured.")
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT * FROM students WHERE email=%s", (email,))
            s = cur.fetchone(); cur.close(); conn.close()
            if s and check_password_hash(s["password_hash"], pw):
                session["sid"] = s["id"]
                log_event(s["id"], "login", {"email": email})
                # Admin goes straight to analytics
                if email == ADMIN_EMAIL:
                    return redirect(url_for("analytics_page"))
                return redirect(url_for("dashboard"))
            return render_template("login.html", error="Invalid email or password.")
        return render_template("login.html", error=None)
    except Exception as e:
        print(f"login error: {e}"); traceback.print_exc()
        return render_template("login.html", error=f"Something went wrong: {e}")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("landing"))

# ── Pages ─────────────────────────────────────────────────
@app.route("/dashboard")
def dashboard():
    try:
        s = current_student()
        if not s: return redirect(url_for("login"))
        docs = get_docs(s["id"])
        log_event(s["id"], "page_view", {"page":"dashboard"})
        return render_template("dashboard.html", s=s, admin_email=ADMIN_EMAIL, docs=docs, active="dashboard")
    except Exception as e:
        print(f"dashboard error: {e}"); traceback.print_exc()
        return f"<h2>Error</h2><pre>{e}</pre><a href='/logout'>Logout</a>", 500

@app.route("/documents")
def documents():
    try:
        s = current_student()
        if not s: return redirect(url_for("login"))
        docs = get_docs(s["id"])
        log_event(s["id"], "page_view", {"page":"documents"})
        return render_template("documents.html", s=s, admin_email=ADMIN_EMAIL, docs=docs, active="documents")
    except Exception as e:
        print(f"documents error: {e}"); traceback.print_exc()
        return f"<h2>Error</h2><pre>{e}</pre><a href='/logout'>Logout</a>", 500

@app.route("/chat-page")
def chat_page():
    try:
        s = current_student()
        if not s: return redirect(url_for("login"))
        docs = get_docs(s["id"])
        log_event(s["id"], "page_view", {"page":"chat"})
        return render_template("chat.html", s=s, admin_email=ADMIN_EMAIL, docs=docs, active="chat")
    except Exception as e:
        print(f"chat_page error: {e}"); traceback.print_exc()
        return f"<h2>Error</h2><pre>{e}</pre><a href='/logout'>Logout</a>", 500

@app.route("/analytics-page")
def analytics_page():
    try:
        s = current_student()
        if not s: return redirect(url_for("login"))
        if s["email"].lower() != ADMIN_EMAIL:
            return redirect(url_for("dashboard"))
        log_event(s["id"], "page_view", {"page":"analytics"})
        return render_template("analytics.html", s=s, admin_email=ADMIN_EMAIL, active="analytics")
    except Exception as e:
        print(f"analytics_page error: {e}"); traceback.print_exc()
        return f"<h2>Error</h2><pre>{e}</pre><a href='/logout'>Logout</a>", 500

# ── API ───────────────────────────────────────────────────
@app.route("/upload", methods=["POST"])
def upload_file():
    try:
        s = current_student()
        if not s: return jsonify({"error":"Not logged in"}), 401
        if "file" not in request.files:
            return jsonify({"error":"No file"}), 400
        file   = request.files["file"]
        course = request.form.get("course","General")
        if not file or not file.filename:
            return jsonify({"error":"No file selected"}), 400
        ext = file.filename.rsplit(".",1)[-1].lower() if "." in file.filename else ""
        if ext not in ALLOWED_EXT:
            return jsonify({"error":f"File type .{ext} not allowed"}), 400
        folder = os.path.join(UPLOAD_FOLDER, str(s["id"]))
        os.makedirs(folder, exist_ok=True)
        orig  = file.filename
        saved = f"{uuid.uuid4().hex[:8]}_{secure_filename(orig)}"
        path  = os.path.join(folder, saved)
        file.save(path)
        size    = os.path.getsize(path)
        content = extract_text(path, orig)
        print(f"UPLOAD: {orig} → {len(content)} chars extracted")
        if DB_URL:
            conn = get_db(); cur = conn.cursor()
            cur.execute("""INSERT INTO documents
                           (student_id,filename,orig_name,course,size_bytes,content)
                           VALUES(%s,%s,%s,%s,%s,%s)""",
                        (s["id"], saved, orig, course, size, content))
            conn.commit(); cur.close(); conn.close()
        log_event(s["id"], "file_uploaded", {"name":orig,"course":course,"chars":len(content)})
        return jsonify({"success":True, "docs":get_docs(s["id"]), "chars_extracted":len(content)})
    except Exception as e:
        print(f"upload error: {e}"); traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/delete-file", methods=["POST"])
def delete_file():
    try:
        s = current_student()
        if not s: return jsonify({"error":"Not logged in"}), 401
        doc_id = (request.get_json() or {}).get("doc_id")
        if DB_URL and doc_id:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT filename FROM documents WHERE id=%s AND student_id=%s", (doc_id, s["id"]))
            doc = cur.fetchone()
            if doc:
                fp = os.path.join(UPLOAD_FOLDER, str(s["id"]), doc["filename"])
                if os.path.exists(fp): os.remove(fp)
                cur.execute("DELETE FROM documents WHERE id=%s", (doc_id,))
                conn.commit()
                log_event(s["id"], "file_deleted", {"doc_id": doc_id})
            cur.close(); conn.close()
        return jsonify({"success":True, "docs":get_docs(s["id"])})
    except Exception as e:
        print(f"delete error: {e}"); traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/chat", methods=["POST"])
def chat():
    try:
        s = current_student()
        if not s: return jsonify({"error":"Not logged in"}), 401
        if not ANTHROPIC_API_KEY:
            return jsonify({"error":"ANTHROPIC_API_KEY not set"}), 500
        data     = request.get_json() or {}
        messages = data.get("messages", [])
        user_msg = messages[-1]["content"] if messages else ""
        log_event(s["id"], "question_asked", {"q": user_msg[:200]})
        docs    = get_docs(s["id"])
        doc_ctx = build_doc_context(docs)
        import datetime
        now   = datetime.datetime.now()
        today = now.strftime("%A, %B %d, %Y")
        system = (
            f"You are WINK, a warm encouraging AI academic companion for UTEP students. "
            f"Today's date is {today}. Always use this when answering questions about "
            f"deadlines, schedules, or anything time-related. "
            f"You are helping {s['first_name']} {s['last_name']}, "
            f"a {s['classification']} majoring in {s['major']}. "
            f"IMPORTANT: The student's actual uploaded course documents are included below. "
            f"Always read and reference the document content to answer questions. "
            f"Quote specific deadlines, requirements, and grading criteria directly from their documents. "
            f"UTEP resources: University Writing Center, CASS Tutoring, Advising & Student Support. "
            f"Be warm, specific, and actionable. End with an encouraging note."
            + doc_ctx
        )
        import httpx, anthropic as ac
        client = ac.Anthropic(api_key=ANTHROPIC_API_KEY, http_client=httpx.Client())
        resp   = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500, system=system, messages=messages
        )
        reply = resp.content[0].text
        log_event(s["id"], "answer_given", {"len": len(reply), "full_answer": reply})
        return jsonify({"reply": reply})
    except Exception as e:
        print(f"chat error: {e}"); traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/debug-docs")
def debug_docs():
    s = current_student()
    if not s: return redirect(url_for("login"))
    docs = get_docs(s["id"])
    out = f"<h2>WINK Document Debug — {s['first_name']} {s['last_name']}</h2>"
    out += f"<p><strong>{len(docs)} document(s) uploaded</strong></p>"
    for d in docs:
        content = (d.get("content") or "").strip()
        out += f"<hr><h3>{d['orig_name']}</h3>"
        out += f"<p>Course: {d['course']} | Size: {round(d.get('size_bytes',0)/1024,1)} KB | "
        out += f"Characters extracted: <strong>{len(content)}</strong></p>"
        if content:
            preview = content[:2000].replace("<","&lt;").replace(">","&gt;")
            out += f"<pre style='background:#f0f0f0;padding:12px;border-radius:8px;white-space:pre-wrap;'>{preview}</pre>"
            if len(content) > 2000:
                out += f"<p><em>...and {len(content)-2000} more characters</em></p>"
        else:
            out += "<p style='color:red;'><strong>No text extracted from this file!</strong></p>"
    out += "<hr><p><a href='/documents'>Back to Documents</a></p>"
    return out

@app.route("/analytics-data")
def analytics_data():
    try:
        s = current_student()
        if not s: return jsonify({"error":"Not logged in"}), 401
        if not DB_URL: return jsonify({"error":"No database"}), 500
        conn = get_db(); cur = conn.cursor()

        cur.execute("SELECT COUNT(*) as n FROM students")
        total_s = cur.fetchone()["n"]

        cur.execute("SELECT COUNT(*) as n FROM events WHERE event_type IN ('login','account_created')")
        total_sess = cur.fetchone()["n"]

        cur.execute("SELECT COUNT(*) as n FROM events WHERE event_type='question_asked'")
        total_q = cur.fetchone()["n"]

        cur.execute("SELECT COUNT(*) as n FROM events WHERE event_type='file_uploaded'")
        total_up = cur.fetchone()["n"]

        # Per-student summary
        cur.execute("""
            SELECT
                s.id, s.first_name, s.last_name, s.email,
                s.classification, s.major,
                to_char(s.created_at, 'Mon DD YYYY') as joined,
                (SELECT COUNT(*) FROM events e WHERE e.student_id=s.id
                 AND e.event_type IN ('login','account_created')) as sessions,
                (SELECT COUNT(*) FROM events e WHERE e.student_id=s.id
                 AND e.event_type='question_asked') as questions,
                (SELECT COUNT(*) FROM events e WHERE e.student_id=s.id
                 AND e.event_type='file_uploaded') as uploads,
                (SELECT COUNT(*) FROM documents d WHERE d.student_id=s.id) as docs
            FROM students s
            ORDER BY s.created_at DESC
        """)
        students = [dict(r) for r in cur.fetchall()]

        # Recent events feed
        cur.execute("""
            SELECT
                e.id, e.event_type, e.payload,
                to_char(e.created_at, 'Mon DD HH24:MI') as ts,
                s.first_name, s.last_name, s.email
            FROM events e
            LEFT JOIN students s ON s.id = e.student_id
            ORDER BY e.created_at DESC
            LIMIT 60
        """)
        recent = []
        for r in cur.fetchall():
            row = dict(r)
            row["payload"] = safe_payload(row.get("payload"))
            recent.append(row)

        cur.execute("SELECT major, COUNT(*) as n FROM students GROUP BY major ORDER BY n DESC")
        by_major = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT classification, COUNT(*) as n FROM students GROUP BY classification ORDER BY n DESC")
        by_class = [dict(r) for r in cur.fetchall()]

        cur.close(); conn.close()
        return jsonify({
            "total_students":  total_s,
            "total_sessions":  total_sess,
            "total_questions": total_q,
            "total_uploads":   total_up,
            "students":        students,
            "recent":          recent,
            "by_major":        by_major,
            "by_class":        by_class
        })
    except Exception as e:
        print(f"analytics_data error: {e}"); traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/analytics-data-full")
def analytics_data_full():
    try:
        s = current_student()
        if not s: return jsonify({"error":"Not logged in"}), 401
        if s["email"].lower() != ADMIN_EMAIL: return jsonify({"error":"Not authorized"}), 403
        if not DB_URL: return jsonify({"error":"No database"}), 500
        conn = get_db(); cur = conn.cursor()

        cur.execute("SELECT COUNT(*) as n FROM students"); total_s = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) as n FROM events WHERE event_type IN ('login','account_created')"); total_sess = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) as n FROM events WHERE event_type='question_asked'"); total_q = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) as n FROM events WHERE event_type='file_uploaded'"); total_up = cur.fetchone()["n"]

        # Per-student summary
        cur.execute("""
            SELECT s.id, s.first_name, s.last_name, s.email, s.classification, s.major,
                   to_char(s.created_at,'Mon DD YYYY') as joined,
                   (SELECT COUNT(*) FROM events e WHERE e.student_id=s.id AND e.event_type IN ('login','account_created')) as sessions,
                   (SELECT COUNT(*) FROM events e WHERE e.student_id=s.id AND e.event_type='question_asked') as questions,
                   (SELECT COUNT(*) FROM events e WHERE e.student_id=s.id AND e.event_type='file_uploaded') as uploads,
                   (SELECT COUNT(*) FROM documents d WHERE d.student_id=s.id) as docs
            FROM students s ORDER BY s.created_at DESC""")
        students = [dict(r) for r in cur.fetchall()]

        # Full questions list (no truncation)
        cur.execute("""
            SELECT e.payload, to_char(e.created_at,'Mon DD HH24:MI') as ts,
                   s.first_name, s.last_name, s.email
            FROM events e LEFT JOIN students s ON s.id=e.student_id
            WHERE e.event_type='question_asked'
            ORDER BY e.created_at DESC LIMIT 200""")
        questions = []
        for r in cur.fetchall():
            row = dict(r)
            p = safe_payload(row.get("payload"))
            questions.append({
                "first_name": row.get("first_name",""),
                "last_name":  row.get("last_name",""),
                "email":      row.get("email",""),
                "question":   p.get("q",""),
                "ts":         row.get("ts","")
            })

        # Paired Q&A conversations
        cur.execute("""
            SELECT e.id, e.event_type, e.payload, e.created_at,
                   to_char(e.created_at,'Mon DD HH24:MI') as ts,
                   s.first_name, s.last_name, s.email, s.id as sid
            FROM events e LEFT JOIN students s ON s.id=e.student_id
            WHERE e.event_type IN ('question_asked','answer_given')
            ORDER BY s.id, e.created_at ASC LIMIT 400""")
        raw_events = [dict(r) for r in cur.fetchall()]
        conversations = []
        i = 0
        while i < len(raw_events):
            ev = raw_events[i]
            p  = safe_payload(ev.get("payload"))
            if ev["event_type"] == "question_asked":
                conv = {
                    "first_name": ev.get("first_name",""),
                    "last_name":  ev.get("last_name",""),
                    "email":      ev.get("email",""),
                    "question":   p.get("q",""),
                    "answer":     "",
                    "ts":         ev.get("ts",""),
                    "sid":        ev.get("sid")
                }
                if i+1 < len(raw_events) and raw_events[i+1]["event_type"] == "answer_given" and raw_events[i+1].get("sid") == ev.get("sid"):
                    ap = safe_payload(raw_events[i+1].get("payload"))
                    conv["answer"] = ap.get("full_answer", "")
                    i += 2
                else:
                    i += 1
                conversations.append(conv)
            else:
                i += 1

        # Recent activity feed (last 100)
        cur.execute("""
            SELECT e.event_type, e.payload, to_char(e.created_at,'Mon DD HH24:MI') as ts,
                   s.first_name, s.last_name, s.email
            FROM events e LEFT JOIN students s ON s.id=e.student_id
            ORDER BY e.created_at DESC LIMIT 100""")
        recent = []
        for r in cur.fetchall():
            row = dict(r)
            row["payload"] = safe_payload(row.get("payload"))
            recent.append(row)

        # By major / classification
        cur.execute("SELECT major, COUNT(*) as n FROM students GROUP BY major ORDER BY n DESC")
        by_major = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT classification, COUNT(*) as n FROM students GROUP BY classification ORDER BY n DESC")
        by_class = [dict(r) for r in cur.fetchall()]

        # By course (documents)
        cur.execute("SELECT course, COUNT(*) as n FROM documents GROUP BY course ORDER BY n DESC")
        by_course = [dict(r) for r in cur.fetchall()]

        # Daily usage last 7 days
        cur.execute("""
            SELECT to_char(created_at,'Mon DD') as day, COUNT(*) as n
            FROM events
            WHERE created_at >= NOW() - INTERVAL '7 days'
            GROUP BY to_char(created_at,'Mon DD'), DATE(created_at)
            ORDER BY DATE(created_at) ASC""")
        daily = [dict(r) for r in cur.fetchall()]

        cur.close(); conn.close()
        return jsonify({
            "total_students":  total_s,
            "total_sessions":  total_sess,
            "total_questions": total_q,
            "total_uploads":   total_up,
            "students":        students,
            "questions":       questions,
            "conversations":   conversations,
            "recent":          recent,
            "by_major":        by_major,
            "by_class":        by_class,
            "by_course":       by_course,
            "daily":           daily
        })
    except Exception as e:
        print(f"analytics_data_full error: {e}"); traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/student-conversations/<int:sid>")
def student_conversations(sid):
    try:
        s = current_student()
        if not s: return jsonify({"error":"Not logged in"}), 401
        if s["email"].lower() != ADMIN_EMAIL: return jsonify({"error":"Not authorized"}), 403
        if not DB_URL: return jsonify({"error":"No database"}), 500
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            SELECT e.event_type, e.payload, to_char(e.created_at,'Mon DD HH24:MI') as ts
            FROM events e
            WHERE e.student_id=%s AND e.event_type IN ('question_asked','answer_given')
            ORDER BY e.created_at ASC""", (sid,))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close(); conn.close()
        conversations = []
        i = 0
        while i < len(rows):
            ev = rows[i]
            p  = safe_payload(ev.get("payload"))
            if ev["event_type"] == "question_asked":
                conv = {"question": p.get("q",""), "answer":"", "ts": ev.get("ts","")}
                if i+1 < len(rows) and rows[i+1]["event_type"] == "answer_given":
                    ap = safe_payload(rows[i+1].get("payload"))
                    conv["answer"] = ap.get("full_answer","")
                    i += 2
                else:
                    i += 1
                conversations.append(conv)
            else:
                i += 1
        return jsonify({"conversations": conversations})
    except Exception as e:
        print(f"student_conversations error: {e}"); traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/health")
def health():
    db_ok = False
    event_count = 0
    student_count = 0
    if DB_URL:
        try:
            conn = get_db(); cur = conn.cursor()
            db_ok = True
            cur.execute("SELECT COUNT(*) as n FROM events")
            event_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) as n FROM students")
            student_count = cur.fetchone()["n"]
            cur.close(); conn.close()
        except Exception as e:
            print(f"health check db error: {e}")
    return jsonify({
        "status":        "ok",
        "db":            db_ok,
        "db_url":        bool(DB_URL),
        "api_key":       bool(ANTHROPIC_API_KEY),
        "total_events":  event_count,
        "total_students": student_count
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)), debug=False)
