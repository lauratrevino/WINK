import json
import secrets
import os
from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, redirect, request, session, url_for
from werkzeug.security import generate_password_hash

from .. import config
from ..extensions import csrf, db_cursor
from ..security import rate_limited
from ..services.cron import cron_job
from ..services.demo import delete_unused_expired_demos, end_demo_session

bp = Blueprint("demo", __name__)
DEMO_TTL_HOURS = 1


def _purge_expired(cur):
    """Ends expired demo sessions WITHOUT deleting anything -- see
    services/demo.py's end_demo_session for why. is_active=FALSE is what
    actually stops this from re-matching on the next run (demo_expires_at
    alone would just keep re-selecting the same rows forever) and also
    removes it from 'Active Right Now' in the demo usage stats. Returns
    the number of sessions actually ended, so callers don't need (and
    can't get out of sync with) a separate count query -- see
    purge_expired_demos_cron's history for what happens when the two
    counts disagree."""
    cur.execute("SELECT id FROM students WHERE is_demo=TRUE AND is_active=TRUE AND demo_expires_at < NOW()")
    expired = [r["id"] for r in cur.fetchall()]
    for sid in expired:
        end_demo_session(cur, sid, "expired")
    # Also clears out older expired demos that never used the AI (ones
    # that ended before this rule existed, or via logout).
    delete_unused_expired_demos(cur)
    return len(expired)


def delete_demo_student(student_id, reason="logout"):
    """Historically hard-deleted the demo account on logout/replacement.
    Now just ends the session the same non-destructive way expiry does
    (services/demo.py's end_demo_session) -- nothing is deleted, so the
    demo's statistics and full conversation history stay available in
    Analytics."""
    if not student_id or not config.DB_URL:
        return
    with db_cursor(commit=True) as cur:
        end_demo_session(cur, student_id, reason)


def _seed_demo(cur, sid):
    today = date.today()
    # 4th field is a doc_type — must be one of config.DOC_TYPES' actual slugs
    # (lowercase/underscored), not a display label, since it's now stored
    # as-is rather than hardcoded (see the INSERT below). "Study Guide" has
    # no matching slug in DOC_TYPES, so it maps to "other" rather than
    # inventing a category the rest of the app doesn't recognize.
    docs = [
        ("UNIV 1301", "10196", "UNIV1301FallSyllabus2026.docx", "syllabus",
         """UNIV 1301: First-Year Seminar
"Designing Your College Experience"
Demo State University (a fictional school used for the My WINK demo)
Instructor: Dr. Jordan Rivera
Email: jrivera@demostate.example
Office: Student Success Center, Room 210
Office Hours: MWF 8:00-9:30 a.m. and 11:00 a.m.-12:00 p.m., or by appointment
Course Description
UNIV 1301 helps first-year students build a foundation for academic success, personal growth, and professional readiness. Students explore four course themes, Identity, Agency, Belonging, and Aspirations, while developing study skills, teamwork, and an entrepreneurial mindset: identifying strengths, using resources, solving problems, and designing their own path.
Learning Objectives
Identity: explore your strengths, values, and goals as a learner.
Agency: take ownership of your academic path and decisions; practice leadership, communication, and teamwork.
Belonging: build a network of support and connection on campus.
Aspirations: clarify academic and career goals and map the steps to reach them.
Use of AI Tools
AI tools are required in this course. You will use AI for idea generation, editing, organizing, project management, and productivity, while learning to use these tools ethically and responsibly. Cite AI-generated content, verify AI output, and never submit AI work as entirely your own thinking. Unethical use of AI will be handled under the university academic integrity policy.
WINK (What I Need to Know) is used in this course for organization, course questions, studying, project management, and planning.
Required Text
The Moth Presents: All These Wonders (edited by Catherine Burns). Readings provided in class; do not buy.
Major Assignments & Points
Attendance | 100
Common Read Participation | 100
Entrepreneurial Mindset Activities | 100
First-Year Story Group Project | 300
Strengths Assessment | 25
Survivor Series | 100
Career Activity | 25
Peer Leader Group Meeting | 20
Syllabus Quiz | 10
Syllabus Addendum | 10
Campus Engagement Activity | 100
Choices 360 | 25
Course Evaluations | 10
First-Year Event | 75
TOTAL | 1000
Grading Scale
A | 900-1000
B | 800-899
C | 700-799
D | 600-699
F | 0-599
First-Year Story Group Project (the major course project)
This 300-point team project is the most important assignment in the course. Your team of three will create a 10-15 minute video that tells the real story of your first year of college for future students. You build it in stages, with each deliverable aligned to the course themes.
Deliverable 1 (due Sunday, Sep. 20, 5:00 p.m.): Project management plan, including:
1. A cover sheet (project title, course, section, instructor, team members, date)
2. A work breakdown structure (WBS)
3. A Gantt chart of the project schedule
4. References for any use of AI tools (APA 7)
5. A detailed task list for each team member
6. The introduction and the first two slides of the video storyboard
Later deliverables: Identity section, Agency section, Belonging section, Aspirations and final thoughts, final video, and a showcase presentation.
Course Policies
No late work. Late work is not graded unless approved with medical documentation. Deadlines are Sundays at 5:00 p.m.
Attendance is mandatory. Two absences are allowed for mental health; students may be dropped on the third absence. Two late arrivals of more than 10 minutes equal one absence.
No phones or laptops during lectures unless an activity calls for them.
Emails to the instructor must include your CRN in the subject line.
Assignments must be submitted as Word (.docx) or PDF files.
The syllabus may change; changes are announced in class and on the course site.
Grievances
Talk with your instructor first. If a concern is not resolved, contact Dr. Morgan Lee, Director of First-Year Programs (mlee@demostate.example, 555-0142, Student Success Center Room 300).
Course Sections and Team
CRN #10248 | MWF 9:30-10:20 a.m. | Learning Center 208 | Peer Leader: Sam Lopez (slopez@demostate.example)
CRN #10196 | MWF 12:30-1:20 p.m. | Education Building 318 | Peer Leader: Maya Chen (mchen@demostate.example)
CRN #10247 | TR 7:30-8:50 a.m. | Learning Center 334 | Peer Leader: Maya Chen (mchen@demostate.example)
Campus Resources (Demo State University)
Academic Advising Center | advising@demostate.example | 555-0110 | Student Union 120 | Mon-Fri 8 a.m.-5 p.m.
Counseling Center | counseling@demostate.example | 555-0120 (24/7 crisis line) | Health Building 200
Accessibility Services | access@demostate.example | 555-0130 | Student Union 106
Tutoring and Writing Center | tutoring@demostate.example | 555-0140 | Library 2nd floor | Mon-Thu 9 a.m.-8 p.m.
Financial Aid Office | finaid@demostate.example | 555-0150 | Administration Building 101
Career Center | careers@demostate.example | 555-0160 | Student Union 220
Food Pantry | pantry@demostate.example | Student Union 015 | Tue and Thu 10 a.m.-2 p.m.
Campus Police (non-emergency) | 555-0199 | Emergency: 911
Accommodations
Students with disabilities who need accommodations should contact Accessibility Services at 555-0130 or access@demostate.example.
Academic Integrity
Academic dishonesty is not tolerated. Suspected cases are reported to the Office of Student Conduct.
Financial Aid Reminder
Students receiving financial aid must confirm attendance in each course during the first two weeks of the semester to keep full funding."""),
        ("UNIV 1301", "10196", "UNIV_1301_Fall2026_CalendarMWF.docx", "course_calendar",
         """CRN #10248   MWF, 9:30 – 10:20 a.m.   ·   Learning Center 208
CRN #10196   MWF, 12:30 – 1:20 p.m. - Education 318
CRN #10247   TT, 7:30 – 8:50 a.m.   ·   Learning Center 334
Course-theme color key above.  Red text = critical deadline.  Purple text = Peer Leader–led item.
FALL SEMESTER CALENDAR 2026
IDENTITY | AGENCY | ASPIRATION | BELONGING
WEEK | DATE & DAY | TOPIC / ACTIVITY | LOCATION | DEADLINE
1. | Mon.  Aug. 24 | CREATE GROUPS Sep. 9th – CRITICAL DECISION!!!!! | Anything not completed in class is due the Sunday after class.  NO EXCEPTIONS!!!!!
IDENTITY | Wed. Aug. 26 | Intro – Peer Leader Intro (slides in PowerPoint Presentations)
📌 Pick Groups (Sep. 9– MOST IMPORTANT DECISION OF YOUR LIFE!!!) | In-Class
Fri. Aug. 28 | 🖥 Intro to ChatGPT – Download free version
📅 Schedule PL Meeting (no later than Oct. 4; must meet in person)
📝 Syllabus Quiz & Contract
✨ Campus Engagement Quiz | In-Class | Sunday, Aug. 30th, 5:00 p.m.
2 – IDENTITY | Mon. Aug. 31 | ChatGPT and Adobe Express Tutorials
Wed. Sep. 2 | 💻 ChatGPT / Adobe Express Tutorials (Bring laptop) | In-Class
3 – IDENTITY | Fri. Sep. 4 | 📅 Schedule First-Year Event on the campus events page. You must attend one.

PEER LEADER ACTIVITIES – YOU MUST ATTEND ONE.  Do it early!!!!! | In-Class
Mon. Sep. 7 | Labor Day!!  NO CLASS!! | In-Class
4 – IDENTITY | Wed. Sep. 9 | 👥 Create Groups (critical decision)
Group Project Instructions & Discussion
📑 First two group slides assigned | In-Class | Sun. Sep. 20, 5 PM – Team organization and First 2 slides due
Fri. Sep. 11 | Group Project Day
5 – AGENCY | Mon Sep. 14 | 📚 Book Club #1 Discussion Entrepreneurial Mindset
💡 Entrepreneurial Mindset 1 PPT & Quiz | In-Class | Sun. Sep. 20, 5 PM
Wed Sep. 16 | WINK & Project Management Intro Slides and Project – Deliverable 1
📅 Meet with Peer Leader | Intro slides and Project  Management Due Sunday, Sep. 20,
6 – AGENCY | Fri.
Sep. 18 | Group Project Day – Identity Section | Sun. Sep. 27, 5 PM
Mon Sep. 21 | 🎤 Elevator Pitch (Peer Leader)
📅 Schedule First-Year Event (campus events page)
PEER LEADER ACTIVITIES – YOU MUST ATTEND ONE.  Do it early!!!!! | In-Class
7 – AGENCY | Wed. Sep. 23 | Group Project Day – Agency Section
Agency slides due Oct. 11 | EM2 Due Sunday Sep. 27, 5:00 p.m.  Agency slides due Oct. 11
Fri. Sep. 25 | Entrepreneurial Mindset PPT #2 Survey
Group Project Day | In-Class | Sun. Sep. 27, 5 PM
8 – ASPIRATION | Mon Sep. 28 | Entrepreneurial Mindset #3 & Survey | Sun. Oct. 4, 5 PM
Wed Sep. 30 | Group Project  - Belonging.  Section
Slides Due Oct. 25
9 – ASPIRATION | Fri. Oct. 2 | Peer Leader Meeting
Group Project | Sunday, Oct. 4,
Mon. Oct. 5 | Entrepreneurial Mindset #4 & Survey | Sun. Oct. 11, 5 PM – EM4 Survey due
10 – BELONGING | Wed. Oct. 7 | Entrepreneurial Mindset #5 & Survey
Group Project – Review Sections
SURVIVOR SERIES | Sun. Oct. 11, 5 PM – EM5 Survey due
Sun. Oct. 11, 5 PM – All sections due
Fri. Oct. 9 | Group Project Day – Belonging | In-Class | Sun. Oct. 25, 5:00 P.M.
Oct. 30 | DROP DAY | Oct. 30 – Last day to drop
11 – BELONGING | Mon Oct. 12 | 📚 Book Club #4 | Learning Center | Book Club Essay Due Oct. 25, 5:00 p.m.
Wed Oct. 14 | Due Sunday, Oct. 18
Fri. Oct. 16 | Group Project Day…Aspirations & Final Thoughts
Slides due Nov. 1st
Oct. 19- Oct. 23 | FALL BREAK!!!! | Sunday, Oct. 25, Belonging slides due.
12 – BELONGING | Mon Oct. 26 | Peer Leader arranged Study Abroad presentation.
🧭 Choices 360
Wed Oct. 28 | 💡 Entrepreneurial Mindset Post-Survey | Sun. Nov. 1, 5 PM – Post-Survey & Choices360 due
13 – BELONGING | Friday Oct. 30 | DO NOT MEET IN CLASS Group Project Day. | Sunday, Nov. 1st Aspirations and Final Thought Slides Due
Mon. Nov. 2 | Group Project  - Final Review | Nov. 8th MUST TURN IN FINAL
14 – BELONGING | Wed. Nov. 4 | Book Club #2
https://stories.phdproject.org/
Fri. Nov. 6 | Strengths Assessment | Sunday, Nov. 8th, Group Project Review Due
Mon. Nov. 9 | SURVIVOR SERIES – DO NOT ATTEND CLASS
Wed. Nov. 11 | Book Club #3
https://stories.phdproject.org/
Fri. Nov. 13 | Group Project Day  DUE SUNDAY | Nov. 15- Projects due
Mon. Nov. 16 | Book Club #4
Book Club Essay DUE  NOVEMBER | Book Club Essay Due Nov. 22nd
Wednesday. Nov. 18 | Vibe Coding For Fun – Bring your picture and computer.  Download Runway and Replit.
Friday, Nov. 20 | MAKEUP DAY  - 2 per person
Mon. Nov. 23 | Showcase
Wed. Nov. 25 | Showcase
Friday, Nov. 27 | Showcase
Monday, Nov. 30 | Course Evaluations – MUST ATTEND CLASS
Wed. Dec. 2 | Electronic Assignment – Submit Final Group Project Documents | DO NOT MEET IN CLASS
Friday, Dec. 4 | 2 makeup assignments | DO NOT MEET IN CLASS
15 – BELONGING | Mon Dec. 7 | Stress Management Presentation arranged by Peer Leader
Wed. Dec. 9 | Last Day of Class
Dec. 11 | Last Day of Classes | Fri Dec. 4 – Dead Day
16 – BELONGING | Mon–Fri Dec. 14-18 | Final Exams | In-Class
Tue Dec. 15 | Grades Due | Tue Dec. 15 – Grades Due"""),
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

    # doc_ids indices now that the old UNIV 1301 .txt file is gone:
    # 0 = UNIV 1301 syllabus (.docx), 1 = UNIV 1301 calendar (.docx),
    # 2 = MATH 1324, 3 = HIST 1301, 4 = BIOL 1305. UNIV 1301 deadlines
    # are linked to the calendar doc (index 1) since that's the document
    # they're logically drawn from.
    #
    # UNIV 1301's deadlines are hardcoded here rather than extracted live
    # via extract_deadlines() from the seeded calendar text: that text is
    # fixed and identical for every demo session, so a live extraction
    # call would always produce the same output while adding a
    # multi-second synchronous API round trip to the critical path of
    # every /demo/start request (the visible delay between clicking
    # Continue and landing on the documents page). If a future edit
    # changes the seeded calendar content above, this list needs to be
    # updated to match by hand.
    univ_deadlines = [
        (1,"UNIV 1301","Team Organization & First Group Project Slides",date(2026,9,20),"confirmed",False,""),
        (1,"UNIV 1301","Entrepreneurial Mindset 2 (EM2) Survey",date(2026,9,27),"confirmed",False,""),
        (1,"UNIV 1301","Survivor Series — All Sections Due",date(2026,10,11),"confirmed",False,""),
        (1,"UNIV 1301","Belonging Slides & Book Club Essay",date(2026,10,25),"confirmed",False,""),
        (1,"UNIV 1301","Choices 360 & EM Post-Survey",date(2026,11,1),"confirmed",False,""),
        (1,"UNIV 1301","Strengths Assessment",date(2026,11,8),"confirmed",False,""),
        (1,"UNIV 1301","First-Year Story Group Project — Final Turn-In",date(2026,11,8),"confirmed",False,""),
    ]

    deadlines = [
        (2,"MATH 1324","Homework: Functions",today+timedelta(days=2),"confirmed",False,""),
        (3,"HIST 1301","Primary Source Analysis",today+timedelta(days=2),"confirmed",False,""),
        (4,"BIOL 1305","Chapter 4 Quiz",today+timedelta(days=3),"corrected",False,""),
        (2,"MATH 1324","Exam 1",today+timedelta(days=6),"confirmed",False,""),
        (3,"HIST 1301","Reading Response 3",today-timedelta(days=3),"confirmed",True,""),
        (4,"BIOL 1305","Cell Lab Worksheet",today-timedelta(days=5),"confirmed",True,""),
        # Spread further out across the following couple of months — a real
        # semester's deadlines aren't clustered in the first two weeks, and
        # a demo that only ever seeds ~11 days of assignments left the
        # calendar (and the "Study Plan — Next 4 Weeks" feature) looking
        # empty the moment someone browsed past the current month.
        (2,"MATH 1324","Homework: Systems of Equations",today+timedelta(days=16),"confirmed",False,""),
        (3,"HIST 1301","Midterm Exam",today+timedelta(days=21),"confirmed",False,""),
        (4,"BIOL 1305","Genetics Lab Report",today+timedelta(days=24),"confirmed",False,""),
        (2,"MATH 1324","Exam 2",today+timedelta(days=42),"confirmed",False,""),
        (3,"HIST 1301","Reading Response 4",today+timedelta(days=45),"confirmed",False,""),
        (4,"BIOL 1305","Final Project Draft",today+timedelta(days=56),"confirmed",False,""),
        (2,"MATH 1324","Final Exam",today+timedelta(days=70),"confirmed",False,""),
    ] + univ_deadlines
    completed_ids=[]
    for di,course,title,due,status,completed,source_snippet in deadlines:
        cur.execute("""INSERT INTO deadlines(student_id,document_id,course,title,due_date,status,source_snippet,completed)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (sid,doc_ids[di],course,title,due,status,source_snippet or "Sample demo course material",completed))
        did=cur.fetchone()["id"]
        if completed: completed_ids.append((did,due))

    cur.execute("""INSERT INTO deadlines(student_id,course,title,due_date,status,is_personal,color)
                   VALUES(%s,'Personal','Meet with academic advisor',%s,'confirmed',TRUE,'#8B5CF6')""",
                (sid,today+timedelta(days=4)))

    weights={
        # Matches the Fall 2026 UNIV 1301 syllabus's "Major Assignments &
        # Points" table (1000 points total), converted to percentages.
        "UNIV 1301":[("Attendance",10),("Common Read Participation",10),("Entrepreneurial Mindset Activities",10),
                     ("First-Year Story Group Project",30),("Strengths Assessment",2.5),("Survivor Series",10),
                     ("Career Activity",2.5),("Peer Leader Group Meeting",2),("Syllabus Quiz",1),
                     ("Syllabus Addendum",1),("Campus Engagement Activity",10),("Choices 360",2.5),
                     ("Course Evaluations",1),("First-Year Event",7.5)],
        "MATH 1324":[("Homework",25),("Quizzes",15),("Midterm Exams",35),("Final Exam",25)],
        "HIST 1301":[("Reading Responses",20),("Primary Source Analysis",25),("Midterm",25),("Final Project",30)],
        # BIOL 1305 was missing here — since the course dropdown is sorted
        # alphabetically (see grades.py's known_courses), BIOL 1305 sorts
        # first and is the course demo visitors land on by default. Without
        # weights seeded, picking it showed only the bare "pick a course"
        # step instead of the full grading-breakdown experience every other
        # seeded course gets, making the demo look broken/unfinished.
        "BIOL 1305":[("Lab Work",20),("Homework",20),("Quizzes",20),("Exams",40)],
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

    # Every event below is fabricated backstory (backdated up to 7 weeks
    # before this account even existed) purely so a brand-new demo visitor
    # immediately sees a populated dashboard/progress chart instead of an
    # empty one. It is NOT something the visitor did. Each payload carries
    # "seeded": True so admin-facing analytics (real questions asked, real
    # pages visited/time spent) can tell it apart from the visitor's actual
    # activity -- without that flag, these ~24 fake "questions" and ~48
    # fake "page views" per demo session silently swamped the real counts
    # (a visitor who asked exactly 1 real question showed up as having
    # asked 25).
    event_rows=[]
    for weeks_ago in range(7,-1,-1):
        base=datetime.utcnow()-timedelta(weeks=weeks_ago)
        for dayoff in (0,2,4):
            at=base+timedelta(days=dayoff)
            event_rows.extend([
                ("page_view",{"page":"dashboard","seeded":True},at),
                ("page_view",{"page":"calendar","seeded":True},at+timedelta(minutes=2)),
                ("question_asked",{"question":"Sample demo academic question","seeded":True},at+timedelta(minutes=5)),
            ])
        if weeks_ago < 5:
            event_rows.append(("practice_attempt",{"course":"MATH 1324","correct":True,"seeded":True},base+timedelta(days=3)))
    for did,due in completed_ids:
        event_rows.append(("deadline_completed_toggled",{"deadline_id":did,"completed":True,"seeded":True},datetime.combine(due-timedelta(days=1),datetime.min.time())))
    for etype,payload,created in event_rows:
        cur.execute("INSERT INTO events(student_id,event_type,payload,created_at) VALUES(%s,%s,%s,%s)",
                    (sid,etype,json.dumps(payload),created))

    messages=json.dumps([
        {"role":"user","content":"What should I focus on this week?"},
        {"role":"assistant","content":"You have a busy stretch coming up. Start with your UNIV 1301 Strengths Assessment, then your MATH homework, and leave time to review for the biology quiz."}
    ])
    cur.execute("INSERT INTO conversations(student_id,title,messages,updated_at) VALUES(%s,%s,%s,NOW())",
                (sid,"Planning my week",messages))


@bp.route("/demo/start", methods=["POST"])
def start_demo():
    if not config.DB_URL:
        return "Demo mode requires the database.", 503
    # Capped per source IP: each demo session carries up to a 25-call AI
    # budget (see chat.py), so an unbounded number of sessions from one
    # address is a real cost-exposure surface. This is a meaningful
    # limit, not a complete fix — IP-based limits are inherently weak
    # against VPNs/proxies/distributed requests, so a motivated abuser
    # can still route around it — but 3/hour is generous for a real
    # visitor restarting a demo they messed up while keeping the
    # single-IP worst case bounded.
    wait = rate_limited(f"demo-start:{__import__('flask').request.remote_addr}", max_calls=3, window_seconds=3600)
    if wait:
        return "Too many demo sessions started from this connection. Please try again later.", 429
    old_sid=session.get("sid") if session.get("is_demo") else None
    if old_sid:
        delete_demo_student(old_sid, reason="replaced")
    with db_cursor(commit=True) as cur:
        _purge_expired(cur)
        token=secrets.token_hex(8)
        email=f"demo-{token}@wink-demo.invalid"
        cur.execute("""INSERT INTO students(email,password_hash,first_name,last_name,classification,major,university,preferred_language,email_verified,is_active,is_demo,demo_expires_at)
                       VALUES(%s,%s,'DemoWINK','Demo','Freshman','Business','Demo State University','',TRUE,TRUE,TRUE,NOW() + %s * INTERVAL '1 hour') RETURNING id""",
                    (email,generate_password_hash(secrets.token_urlsafe(24)),DEMO_TTL_HOURS))
        sid=cur.fetchone()["id"]
        _seed_demo(cur,sid)
    session.clear(); session.permanent=False
    session["sid"]=sid; session["is_demo"]=True
    return redirect(url_for("documents.documents_page"))


@bp.route("/purge-expired-demos", methods=["POST"])
@csrf.exempt
@cron_job("purge_expired_demos")
def purge_expired_demos_cron(run_id):
    """Independently, reliably cleans up expired demo accounts on a
    schedule, rather than relying only on the opportunistic cleanup that
    happens as a side effect of a new demo starting (_purge_expired()
    called from start_demo() above) or of an expired demo's own session
    being accessed again. Meant to be called by an external scheduler,
    same pattern as /send-deadline-reminders, /send-weekly-digest, and
    /purge-deleted-conversations — same header-based auth, same run
    logging, both centralized in services/cron.py."""
    with db_cursor(commit=True) as cur:
        expired_count = _purge_expired(cur)
    return {"number_processed": expired_count, "purged": expired_count}
