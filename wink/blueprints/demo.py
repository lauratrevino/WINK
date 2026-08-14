import json
import secrets
import os
import shutil
from datetime import date, datetime, timedelta

from flask import Blueprint, redirect, session, url_for
from werkzeug.security import generate_password_hash

from .. import config
from ..extensions import csrf, get_db
from ..security import rate_limited

bp = Blueprint("demo", __name__)
DEMO_TTL_HOURS = 6


def _log_demo_session_ended(cur, student_id, reason):
    try:
        cur.execute("SELECT created_at FROM students WHERE id=%s", (student_id,))
        row = cur.fetchone()
        if not row:
            return
        started_at = row["created_at"]
        cur.execute("SELECT COUNT(*) as n FROM events WHERE student_id=%s AND event_type='question_asked'",
                    (student_id,))
        questions_asked = cur.fetchone()["n"] or 0
        duration_seconds = max(0, int((datetime.utcnow() - started_at).total_seconds()))
        cur.execute("""INSERT INTO demo_sessions(started_at, ended_at, duration_seconds, questions_asked, ended_reason)
                       VALUES (%s, NOW(), %s, %s, %s)""",
                    (started_at, duration_seconds, questions_asked, reason))
    except Exception:
        pass


def _purge_expired(cur):
    cur.execute("SELECT id FROM students WHERE is_demo=TRUE AND demo_expires_at < NOW()")
    expired = [r["id"] for r in cur.fetchall()]
    for sid in expired:
        _log_demo_session_ended(cur, sid, "expired")
        shutil.rmtree(os.path.join(config.UPLOAD_FOLDER, str(sid)), ignore_errors=True)
        cur.execute("DELETE FROM events WHERE student_id=%s", (sid,))
        cur.execute("DELETE FROM students WHERE id=%s AND is_demo=TRUE", (sid,))


def delete_demo_student(student_id, reason="logout"):
    if not student_id or not config.DB_URL:
        return
    shutil.rmtree(os.path.join(config.UPLOAD_FOLDER, str(student_id)), ignore_errors=True)
    conn = get_db(); cur = conn.cursor()
    _log_demo_session_ended(cur, student_id, reason)
    cur.execute("DELETE FROM events WHERE student_id=%s", (student_id,))
    cur.execute("DELETE FROM students WHERE id=%s AND is_demo=TRUE", (student_id,))
    conn.commit(); cur.close()


def _seed_demo(cur, sid):
    today = date.today()
    # 4th field is a doc_type — must be one of config.DOC_TYPES' actual slugs
    # (lowercase/underscored), not a display label, since it's now stored
    # as-is rather than hardcoded (see the INSERT below). "Study Guide" has
    # no matching slug in DOC_TYPES, so it maps to "other" rather than
    # inventing a category the rest of the app doesn't recognize.
    docs = [
        ("UNIV 1301", "12345", "UNIV1301_Syllabus.txt", "syllabus",
         "UNIV 1301 Seminar in Critical Inquiry. Attendance 10%, Edge Activities 20%, Reflection Assignments 30%, Ethnography Project 40%. Office hours Tuesday 2-4 PM. Students should use tutoring and advising resources when needed."),
        ("MATH 1324", "23456", "MATH1324_Syllabus.txt", "syllabus",
         "MATH 1324 Mathematics for Business. Homework 25%, Quizzes 15%, Midterm Exams 35%, Final Exam 25%. Chapters 1-5 cover equations, functions, systems, matrices, and finance applications."),
        ("HIST 1301", "34567", "HIST1301_Calendar.txt", "course_calendar",
         "HIST 1301 U.S. History. Reading responses 20%, Primary Source Analysis 25%, Midterm 25%, Final Project 30%. Topics include colonization, revolution, the early republic, expansion, slavery, and the Civil War."),
        ("BIOL 1305", "45678", "BIOL1305_Study_Guide.txt", "other",
         "BIOL 1305 General Biology study guide: scientific method, cell structure, membranes, metabolism, DNA, genetics, evolution, and ecology. Lab safety and vocabulary review are required."),
    ]
    doc_ids = []
    for course, crn, name, dtype, content in docs:
        cur.execute("""INSERT INTO documents(student_id,filename,orig_name,course,crn,size_bytes,content,doc_type)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (sid, f"demo_{sid}_{name}", name, course, crn, len(content.encode()), content, dtype))
        doc_ids.append(cur.fetchone()["id"])
        demo_dir = os.path.join(config.UPLOAD_FOLDER, str(sid))
        os.makedirs(demo_dir, exist_ok=True)
        with open(os.path.join(demo_dir, f"demo_{sid}_{name}"), "w", encoding="utf-8") as f:
            f.write(content)

    deadlines = [
        (0,"UNIV 1301","Identity Reflection",today+timedelta(days=1),"confirmed",False),
        (1,"MATH 1324","Homework: Functions",today+timedelta(days=2),"confirmed",False),
        (2,"HIST 1301","Primary Source Analysis",today+timedelta(days=2),"confirmed",False),
        (3,"BIOL 1305","Chapter 4 Quiz",today+timedelta(days=3),"corrected",False),
        (0,"UNIV 1301","Career Fair Reflection",today+timedelta(days=5),"confirmed",False),
        (1,"MATH 1324","Exam 1",today+timedelta(days=6),"confirmed",False),
        (2,"HIST 1301","Reading Response 3",today-timedelta(days=3),"confirmed",True),
        (3,"BIOL 1305","Cell Lab Worksheet",today-timedelta(days=5),"confirmed",True),
    ]
    completed_ids=[]
    for di,course,title,due,status,completed in deadlines:
        cur.execute("""INSERT INTO deadlines(student_id,document_id,course,title,due_date,status,source_snippet,completed)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (sid,doc_ids[di],course,title,due,status,"Sample demo course material",completed))
        did=cur.fetchone()["id"]
        if completed: completed_ids.append((did,due))

    cur.execute("""INSERT INTO deadlines(student_id,course,title,due_date,status,is_personal,color)
                   VALUES(%s,'Personal','Meet with academic advisor',%s,'confirmed',TRUE,'#8B5CF6')""",
                (sid,today+timedelta(days=4)))

    weights={
        "UNIV 1301":[("Attendance",10),("Edge Activities",20),("Reflections",30),("Ethnography Project",40)],
        "MATH 1324":[("Homework",25),("Quizzes",15),("Midterm Exams",35),("Final Exam",25)],
        "HIST 1301":[("Reading Responses",20),("Primary Source Analysis",25),("Midterm",25),("Final Project",30)],
    }
    for course, rows in weights.items():
        for i,(cat,w) in enumerate(rows):
            cur.execute("INSERT INTO grading_weights(student_id,course,category,weight,sort_order) VALUES(%s,%s,%s,%s,%s)",
                        (sid,course,cat,w,i))

    practice=[
        ("MATH 1324","What is the slope-intercept form of a line?","y = mx + b","m is slope and b is the y-intercept",2,2),
        ("BIOL 1305","What organelle is the primary site of ATP production?","Mitochondrion","Cellular respiration produces most ATP in mitochondria",3,3),
        ("HIST 1301","What document declared the colonies independent?","Declaration of Independence","Adopted July 4, 1776",1,1),
        ("UNIV 1301","Name one effective help-seeking strategy.","Use office hours or tutoring early.","Seeking help before a crisis supports learning",1,2),
    ]
    for course,q,a,e,interval,streak in practice:
        cur.execute("""INSERT INTO practice_questions(student_id,course,question,answer,explanation,interval_days,correct_streak,next_review_date,last_attempted_at)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,NOW()-INTERVAL '2 days')""",
                    (sid,course,q,a,e,interval,streak,today+timedelta(days=interval)))

    event_rows=[]
    for weeks_ago in range(7,-1,-1):
        base=datetime.utcnow()-timedelta(weeks=weeks_ago)
        for dayoff in (0,2,4):
            at=base+timedelta(days=dayoff)
            event_rows.extend([
                ("page_view",{"page":"dashboard"},at),
                ("page_view",{"page":"calendar"},at+timedelta(minutes=2)),
                ("question_asked",{"question":"Sample demo academic question"},at+timedelta(minutes=5)),
            ])
        if weeks_ago < 5:
            event_rows.append(("practice_attempt",{"course":"MATH 1324","correct":True},base+timedelta(days=3)))
    for did,due in completed_ids:
        event_rows.append(("deadline_completed_toggled",{"deadline_id":did,"completed":True},datetime.combine(due-timedelta(days=1),datetime.min.time())))
    for etype,payload,created in event_rows:
        cur.execute("INSERT INTO events(student_id,event_type,payload,created_at) VALUES(%s,%s,%s,%s)",
                    (sid,etype,json.dumps(payload),created))

    messages=json.dumps([
        {"role":"user","content":"What should I focus on this week?"},
        {"role":"assistant","content":"You have a busy stretch coming up. Start with your UNIV 1301 reflection, then your MATH homework, and leave time to review for the biology quiz."}
    ])
    cur.execute("INSERT INTO conversations(student_id,title,messages,updated_at) VALUES(%s,%s,%s,NOW())",
                (sid,"Planning my week",messages))


@bp.route("/demo/start", methods=["POST"])
@csrf.exempt
def start_demo():
    if not config.DB_URL:
        return "Demo mode requires the database.", 503
    wait = rate_limited(f"demo-start:{__import__('flask').request.remote_addr}", max_calls=5, window_seconds=3600)
    if wait:
        return "Too many demo sessions started from this connection. Please try again later.", 429
    old_sid=session.get("sid") if session.get("is_demo") else None
    if old_sid:
        delete_demo_student(old_sid, reason="replaced")
    conn=get_db(); cur=conn.cursor()
    _purge_expired(cur)
    token=secrets.token_hex(8)
    email=f"demo-{token}@wink-demo.invalid"
    cur.execute("""INSERT INTO students(email,password_hash,first_name,last_name,classification,major,university,preferred_language,email_verified,is_active,is_demo,demo_expires_at)
                   VALUES(%s,%s,'Winkling','Demo','Freshman','Business','University of Texas at El Paso','',TRUE,TRUE,TRUE,NOW() + %s * INTERVAL '1 hour') RETURNING id""",
                (email,generate_password_hash(secrets.token_urlsafe(24)),DEMO_TTL_HOURS))
    sid=cur.fetchone()["id"]
    _seed_demo(cur,sid)
    conn.commit(); cur.close()
    session.clear(); session.permanent=False
    session["sid"]=sid; session["is_demo"]=True
    return redirect(url_for("dashboard.dashboard"))
