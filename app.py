import os, json, uuid, datetime, traceback
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
            id SERIAL PRIMARY KEY, student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            filename TEXT NOT NULL, orig_name TEXT NOT NULL, course TEXT NOT NULL,
            size_bytes INTEGER DEFAULT 0, uploaded_at TIMESTAMP DEFAULT NOW())""")
        cur.execute("""CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY, student_id INTEGER,
            event_type TEXT NOT NULL, payload TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT NOW())""")
        conn.commit(); cur.close(); conn.close()
        print("DB initialized.")
    except Exception as e:
        print(f"DB init error: {e}"); traceback.print_exc()

init_db()

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
    if not DB_URL: return
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("INSERT INTO events(student_id,event_type,payload) VALUES(%s,%s,%s)",
                    (sid, etype, json.dumps(payload or {})))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"log_event error: {e}")

def get_docs(sid):
    if not DB_URL: return []
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM documents WHERE student_id=%s ORDER BY uploaded_at DESC", (sid,))
        docs = [dict(r) for r in cur.fetchall()]; cur.close(); conn.close()
        return docs
    except Exception as e:
        print(f"get_docs error: {e}"); return []

@app.route("/")
def landing():
    try:
        if "sid" in session:
            s = current_student()
            if s: return redirect(url_for("dashboard"))
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
                return err("Database not configured — contact your instructor.")
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
                return redirect(url_for("dashboard"))
            return render_template("login.html", error="Invalid email or password.")
        return render_template("login.html", error=None)
    except Exception as e:
        print(f"login error: {e}"); traceback.print_exc()
        return render_template("login.html", error=f"Something went wrong: {e}")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("landing"))

@app.route("/dashboard")
def dashboard():
    try:
        s = current_student()
        if not s: return redirect(url_for("login"))
        docs = get_docs(s["id"])
        log_event(s["id"], "page_view", {"page":"dashboard"})
        return render_template("dashboard.html", s=s, docs=docs, active="dashboard")
    except Exception as e:
        print(f"dashboard error: {e}"); traceback.print_exc()
        return f"<h2>Dashboard error</h2><pre>{e}</pre><a href='/logout'>Logout</a>", 500

@app.route("/documents")
def documents():
    try:
        s = current_student()
        if not s: return redirect(url_for("login"))
        docs = get_docs(s["id"])
        log_event(s["id"], "page_view", {"page":"documents"})
        return render_template("documents.html", s=s, docs=docs, active="documents")
    except Exception as e:
        print(f"documents error: {e}"); traceback.print_exc()
        return f"<h2>Documents error</h2><pre>{e}</pre><a href='/logout'>Logout</a>", 500

@app.route("/chat-page")
def chat_page():
    try:
        s = current_student()
        if not s: return redirect(url_for("login"))
        docs = get_docs(s["id"])
        log_event(s["id"], "page_view", {"page":"chat"})
        return render_template("chat.html", s=s, docs=docs, active="chat")
    except Exception as e:
        print(f"chat_page error: {e}"); traceback.print_exc()
        return f"<h2>Chat error</h2><pre>{e}</pre><a href='/logout'>Logout</a>", 500

@app.route("/analytics-page")
def analytics_page():
    try:
        s = current_student()
        if not s: return redirect(url_for("login"))
        return render_template("analytics.html", s=s, active="analytics")
    except Exception as e:
        print(f"analytics_page error: {e}"); traceback.print_exc()
        return f"<h2>Analytics error</h2><pre>{e}</pre><a href='/logout'>Logout</a>", 500

@app.route("/upload", methods=["POST"])
def upload_file():
    try:
        s = current_student()
        if not s: return jsonify({"error":"Not logged in"}), 401
        if "file" not in request.files: return jsonify({"error":"No file"}), 400
        file   = request.files["file"]
        course = request.form.get("course","General")
        if not file or not file.filename: return jsonify({"error":"No file selected"}), 400
        ext = file.filename.rsplit(".",1)[-1].lower() if "." in file.filename else ""
        if ext not in ALLOWED_EXT: return jsonify({"error":f"File type .{ext} not allowed"}), 400
        folder = os.path.join(UPLOAD_FOLDER, str(s["id"]))
        os.makedirs(folder, exist_ok=True)
        orig  = file.filename
        saved = f"{uuid.uuid4().hex[:8]}_{secure_filename(orig)}"
        path  = os.path.join(folder, saved)
        file.save(path)
        size  = os.path.getsize(path)
        if DB_URL:
            conn = get_db(); cur = conn.cursor()
            cur.execute("INSERT INTO documents(student_id,filename,orig_name,course,size_bytes) VALUES(%s,%s,%s,%s,%s)",
                        (s["id"], saved, orig, course, size))
            conn.commit(); cur.close(); conn.close()
        log_event(s["id"], "file_uploaded", {"name":orig,"course":course,"kb":round(size/1024,1)})
        return jsonify({"success":True, "docs": get_docs(s["id"])})
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
        return jsonify({"success":True, "docs": get_docs(s["id"])})
    except Exception as e:
        print(f"delete error: {e}"); traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/chat", methods=["POST"])
def chat():
    try:
        s = current_student()
        if not s: return jsonify({"error":"Not logged in"}), 401
        if not ANTHROPIC_API_KEY: return jsonify({"error":"ANTHROPIC_API_KEY not set"}), 500
        data     = request.get_json() or {}
        messages = data.get("messages", [])
        user_msg = messages[-1]["content"] if messages else ""
        log_event(s["id"], "question_asked", {"q": user_msg[:200]})
        docs    = get_docs(s["id"])
        doc_ctx = ""
        if docs:
            doc_ctx = f"\n\nThis student has {len(docs)} uploaded document(s):\n"
            for d in docs:
                doc_ctx += f"  - {d['orig_name']} (Course: {d['course']}, {round(d['size_bytes']/1024,1)} KB)\n"
        system = (
            f"You are WINK, a warm encouraging AI academic companion for UTEP students. "
            f"You are helping {s['first_name']} {s['last_name']}, "
            f"a {s['classification']} majoring in {s['major']}. "
            f"Help with coursework, deadlines, study strategies, campus resources, and college life. "
            f"UTEP resources: University Writing Center, CASS Tutoring, Advising & Student Support. "
            f"Be concise, warm, and actionable. End with an encouraging note." + doc_ctx
        )
        import httpx, anthropic as ac
        client = ac.Anthropic(api_key=ANTHROPIC_API_KEY, http_client=httpx.Client())
        resp   = client.messages.create(model="claude-sonnet-4-20250514",
                                        max_tokens=1000, system=system, messages=messages)
        reply  = resp.content[0].text
        log_event(s["id"], "answer_given", {"len": len(reply)})
        return jsonify({"reply": reply})
    except Exception as e:
        print(f"chat error: {e}"); traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/analytics-data")
def analytics_data():
    try:
        s = current_student()
        if not s: return jsonify({"error":"Not logged in"}), 401
        if not DB_URL: return jsonify({"error":"No database"}), 500
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as n FROM students"); total_s = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) as n FROM events WHERE event_type IN ('login','account_created')"); total_sess = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) as n FROM events WHERE event_type='question_asked'"); total_q = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) as n FROM events WHERE event_type='file_uploaded'"); total_up = cur.fetchone()["n"]
        cur.execute("""
            SELECT s.id,s.first_name,s.last_name,s.email,s.classification,s.major,
                   to_char(s.created_at,'Mon DD YYYY') as joined,
                   COUNT(DISTINCT CASE WHEN e.event_type IN ('login','account_created') THEN e.id END) as sessions,
                   COUNT(DISTINCT CASE WHEN e.event_type='question_asked' THEN e.id END) as questions,
                   COUNT(DISTINCT CASE WHEN e.event_type='file_uploaded' THEN e.id END) as uploads,
                   COUNT(DISTINCT d.id) as docs
            FROM students s
            LEFT JOIN events e ON e.student_id=s.id
            LEFT JOIN documents d ON d.student_id=s.id
            GROUP BY s.id ORDER BY s.created_at DESC""")
        students = [dict(r) for r in cur.fetchall()]
        cur.execute("""
            SELECT e.event_type, e.payload,
                   to_char(e.created_at,'Mon DD HH24:MI') as ts,
                   s.first_name, s.last_name, s.email
            FROM events e LEFT JOIN students s ON s.id=e.student_id
            ORDER BY e.created_at DESC LIMIT 60""")
        recent = []
        for r in cur.fetchall():
            row = dict(r)
            try: row["payload"] = json.loads(row["payload"]) if isinstance(row["payload"],str) else (row["payload"] or {})
            except: row["payload"] = {}
            recent.append(row)
        cur.execute("SELECT major, COUNT(*) as n FROM students GROUP BY major ORDER BY n DESC")
        by_major = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT classification, COUNT(*) as n FROM students GROUP BY classification ORDER BY n DESC")
        by_class = [dict(r) for r in cur.fetchall()]
        cur.close(); conn.close()
        return jsonify({"total_students":total_s,"total_sessions":total_sess,
                        "total_questions":total_q,"total_uploads":total_up,
                        "students":students,"recent":recent,
                        "by_major":by_major,"by_class":by_class})
    except Exception as e:
        print(f"analytics_data error: {e}"); traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/health")
def health():
    db_ok = False
    if DB_URL:
        try:
            conn = get_db(); conn.close(); db_ok = True
        except: pass
    return jsonify({"status":"ok","db":db_ok,"db_url":bool(DB_URL),"api_key":bool(ANTHROPIC_API_KEY)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)), debug=False)
