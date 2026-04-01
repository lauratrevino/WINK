import os
import json
import uuid
import time
import datetime
from flask import (
    Flask, render_template, request, jsonify,
    session, redirect, url_for
)
from werkzeug.utils import secure_filename
import anthropic

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "wink-secret-2025")

# ── Config ──────────────────────────────────────────────
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
DATA_FOLDER   = os.path.join(os.path.dirname(__file__), "data")
ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "txt", "pptx", "xlsx", "png", "jpg", "jpeg"}
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DATA_FOLDER,   exist_ok=True)

app.config["UPLOAD_FOLDER"]      = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

# Anthropic client
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

# ── Helpers ──────────────────────────────────────────────
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def get_student_id():
    if "student_id" not in session:
        session["student_id"] = str(uuid.uuid4())[:8]
    return session["student_id"]

def log_event(event_type, payload=None):
    """Append an analytics event to the JSONL log."""
    sid = get_student_id()
    record = {
        "ts":           datetime.datetime.utcnow().isoformat() + "Z",
        "student_id":   sid,
        "event":        event_type,
        "payload":      payload or {}
    }
    log_path = os.path.join(DATA_FOLDER, "events.jsonl")
    with open(log_path, "a") as f:
        f.write(json.dumps(record) + "\n")
    return record

def get_student_files(student_id):
    """Return list of files uploaded by this student."""
    folder = os.path.join(UPLOAD_FOLDER, student_id)
    if not os.path.exists(folder):
        return []
    files = []
    for fn in os.listdir(folder):
        fp = os.path.join(folder, fn)
        stat = os.stat(fp)
        files.append({
            "name": fn,
            "size": stat.st_size,
            "uploaded": datetime.datetime.utcfromtimestamp(stat.st_mtime).strftime("%b %d, %Y")
        })
    return sorted(files, key=lambda x: x["name"])

def load_analytics():
    """Read events.jsonl and compute summary stats."""
    log_path = os.path.join(DATA_FOLDER, "events.jsonl")
    if not os.path.exists(log_path):
        return {"total_sessions": 0, "total_questions": 0, "total_uploads": 0, "students": {}, "recent": []}

    events = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except Exception:
                    pass

    students   = {}
    questions  = 0
    uploads    = 0
    sessions_s = set()

    for e in events:
        sid = e.get("student_id", "unknown")
        if sid not in students:
            students[sid] = {"questions": 0, "uploads": 0, "sessions": set()}
        et = e.get("event")
        if et == "page_load":
            sessions_s.add(sid + e["ts"][:10])
            students[sid]["sessions"].add(e["ts"][:10])
        elif et == "question_asked":
            questions += 1
            students[sid]["questions"] += 1
        elif et == "file_uploaded":
            uploads += 1
            students[sid]["uploads"] += 1

    # serialise sets
    for s in students.values():
        s["sessions"] = len(s["sessions"])

    return {
        "total_sessions":  len(sessions_s),
        "total_questions": questions,
        "total_uploads":   uploads,
        "total_students":  len(students),
        "students":        students,
        "recent":          events[-20:][::-1]
    }

# ── Routes ───────────────────────────────────────────────

@app.route("/")
def index():
    sid = get_student_id()
    log_event("page_load", {"page": "dashboard"})
    files = get_student_files(sid)
    return render_template("index.html", student_id=sid, files=files)

@app.route("/upload", methods=["POST"])
def upload_file():
    sid = get_student_id()
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files["file"]
    course = request.form.get("course", "General")
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400
    if not allowed_file(file.filename):
        return jsonify({"error": "File type not allowed"}), 400

    student_folder = os.path.join(UPLOAD_FOLDER, sid)
    os.makedirs(student_folder, exist_ok=True)

    filename = secure_filename(file.filename)
    # Prefix with course code to keep organised
    safe_course = secure_filename(course.replace(" ", "_"))
    save_name = f"{safe_course}__{filename}"
    filepath  = os.path.join(student_folder, save_name)
    file.save(filepath)

    size = os.path.getsize(filepath)
    log_event("file_uploaded", {
        "filename": save_name,
        "course":   course,
        "size_kb":  round(size / 1024, 1)
    })

    files = get_student_files(sid)
    return jsonify({"success": True, "filename": save_name, "files": files})

@app.route("/delete-file", methods=["POST"])
def delete_file():
    sid  = get_student_id()
    data = request.get_json()
    filename = secure_filename(data.get("filename", ""))
    filepath = os.path.join(UPLOAD_FOLDER, sid, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        log_event("file_deleted", {"filename": filename})
    files = get_student_files(sid)
    return jsonify({"success": True, "files": files})

@app.route("/chat", methods=["POST"])
def chat():
    sid  = get_student_id()
    data = request.get_json()
    messages    = data.get("messages", [])
    user_msg    = messages[-1]["content"] if messages else ""

    log_event("question_asked", {"question": user_msg[:200]})

    # Build context from uploaded files list
    files = get_student_files(sid)
    file_context = ""
    if files:
        file_context = "\n\nThe student has uploaded these course documents:\n"
        for f in files:
            file_context += f"  - {f['name']} ({round(f['size']/1024,1)} KB)\n"

    system_prompt = (
        "You are WINK — a warm, encouraging AI academic companion for UTEP students "
        "in Dr. Trevino's Entering Student Experience course. "
        "Help students with coursework, deadlines, campus resources, study strategies, "
        "and navigating college life. "
        "UTEP resources: University Writing Center, CASS tutoring center, "
        "Advising & Student Support. "
        "Be concise, warm, and actionable. Always end with a brief encouraging note."
        + file_context
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=system_prompt,
            messages=messages
        )
        reply = response.content[0].text
        log_event("answer_given", {"length": len(reply)})
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/analytics")
def analytics():
    """Simple analytics dashboard for Dr. Trevino."""
    data = load_analytics()
    return render_template("analytics.html", data=data)

@app.route("/analytics/data")
def analytics_data():
    return jsonify(load_analytics())

@app.route("/files")
def list_files():
    sid = get_student_id()
    return jsonify({"files": get_student_files(sid)})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
