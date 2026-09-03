import json
import secrets
import os
from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, redirect, request, session, url_for
from werkzeug.security import generate_password_hash

from .. import config
from ..errors import log_error
from ..extensions import csrf, db_cursor
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
        cur.execute("""INSERT INTO demo_sessions(started_at, ended_at, duration_seconds, questions_asked, ended_reason, student_id)
                       VALUES (%s, NOW(), %s, %s, %s, %s)""",
                    (started_at, duration_seconds, questions_asked, reason, student_id))
    except Exception:
        pass


def _purge_expired(cur):
    """Ends expired demo sessions WITHOUT deleting anything — the account
    row, its uploaded/seeded documents, its events, and its conversations
    are all kept indefinitely so every demo run remains visible in
    Analytics (statistics + full conversation content), not just a
    one-line summary. is_active=FALSE is what actually stops this from
    re-matching on the next run (demo_expires_at alone would just keep
    re-selecting the same rows forever) and also removes it from
    'Active Right Now' in the demo usage stats."""
    cur.execute("SELECT id FROM students WHERE is_demo=TRUE AND is_active=TRUE AND demo_expires_at < NOW()")
    expired = [r["id"] for r in cur.fetchall()]
    for sid in expired:
        _log_demo_session_ended(cur, sid, "expired")
        cur.execute("UPDATE students SET is_active=FALSE WHERE id=%s", (sid,))


def delete_demo_student(student_id, reason="logout"):
    """Historically hard-deleted the demo account on logout/replacement.
    Now just ends the session the same way expiry does (see _purge_expired
    above) — nothing is deleted, so the demo's statistics and full
    conversation history stay available in Analytics."""
    if not student_id or not config.DB_URL:
        return
    with db_cursor(commit=True) as cur:
        _log_demo_session_ended(cur, student_id, reason)
        cur.execute("UPDATE students SET is_active=FALSE WHERE id=%s AND is_demo=TRUE", (student_id,))


def _seed_demo(cur, sid):
    today = date.today()
    # 4th field is a doc_type — must be one of config.DOC_TYPES' actual slugs
    # (lowercase/underscored), not a display label, since it's now stored
    # as-is rather than hardcoded (see the INSERT below). "Study Guide" has
    # no matching slug in DOC_TYPES, so it maps to "other" rather than
    # inventing a category the rest of the app doesn't recognize.
    docs = [
        ("UNIV 1301", "10196", "UNIV1301FallSyllabus2026.docx", "syllabus",
         """UNIV 1301: Seminar in Critical Inquiry
"Designing Your College Experience with an Entrepreneurial Mindset"
Instructor: Dr. Laura Treviño
Email: lhall@utep.edu
Office: Room 108, UGLC
Office Hours: MWF 7:30–9:20 a.m. and 10:30 a.m.–12:20 p.m.
Course Sections
Instructional Team
The UTEP Edge
The UTEP Edge is our philosophy that acknowledges the many assets our students bring to the University. We provide a variety of high-impact experiences both in and out of the classroom through the work of our faculty, staff, alumni, and community partners that build on these assets and talents. Many of the assignments and discussions in this class will further develop the talents you bring to this class, such as communication skills, teamwork, critical thinking, and problem solving.
UTEP Edge Learning Objectives
The UTEP Edge is a university-wide initiative that helps you grow personally, academically, and professionally by building on your unique strengths. It connects you with high-impact experiences like research, internships, community service, and leadership roles. These opportunities are designed to boost your confidence, sharpen your skills, and prepare you for success after graduation — whether that means grad school, a great job, or launching your own path. You'll see UTEP Edge themes show up in this course and throughout your college journey.
Throughout the semester, this course will:
Integrate asset-based approaches into pedagogical practices, curricular and co-curricular programs, and advising
Increase delivery of and participation in high-impact practices and other practices that lead to student success
Promote student engagement in and understanding of the value of reflective practices inside and outside the classroom
Embed professional preparation and readiness in curricular and co-curricular activities
Course Description
As part of the Entering Student Experience, UNIV 1301 supports students as they build a foundation for academic excellence, personal growth, and professional success. In this course, we approach that journey with an entrepreneurial mindset: identifying your strengths, leveraging resources, solving problems, and creating your future through intentional design.
Students will engage in self-reflection, connect with the UTEP community, and develop key academic skills while also learning to think like innovators. Challenges are expected. Resourcefulness is essential. The classroom is your bootcamp for real-world skills, creative thinking, and growth-mindset development.
Learning Objectives
The learning objectives in this course are designed to nurture an entrepreneurial mindset because they help students approach college not just as a series of tasks to complete, but as an opportunity to shape their own path with purpose and intention. By encouraging understanding of identity, agency, belonging, and aspirations, these objectives support students in developing the habits and confidence of innovators — people who know who they are, take initiative, build strong networks, and envision a meaningful future. They're not just about surviving college — they're about learning how to thrive in it and beyond.
Identity Learning Objectives  ·  You, Inc.
Explore your identity as a learner and its connection to your values, beliefs, and goals.
Identify and apply your unique talents and strengths to academic and professional pursuits.
Agency Learning Objectives  ·  Ownership Mindset
Develop ownership over your educational path and decision-making.
Acquire practical strategies to navigate university life and lead within your academic community.
Enhance leadership, communication, and teamwork through collaborative action.
Belonging Learning Objectives  ·  Your Support Ecosystem
Reflect on what belonging means and how to foster it in your college experience.
Build networks of support and connection at UTEP.
Aspiration Learning Objectives  ·  Vision & Mission
Clarify your academic and career goals and map the steps to achieve them.
Envision a post-college future and the legacy you want to create.
Use of AI Tools in the Classroom
Policy Statement
AI tools (including ChatGPT) are required in this course. You will use AI for idea generation, editing, organizing, project management, and productivity enhancement, while learning how to use these tools ethically and responsibly.
Policy Rationale
The purpose of this policy is to promote the integration of innovative AI tools in learning, encouraging students to explore, understand, and utilize these technologies to enhance their academic work. By developing the ability to navigate AI tools responsibly, effectively, and ethically, students not only strengthen their academic skills but also gain a competitive advantage in the workplace. This policy supports students in building the digital fluency and critical thinking needed to thrive in today's evolving professional environments.
Violation of the Policy
Violations of this policy, such as unethical use of AI tools or failure to appropriately cite AI-generated content, will be subject to penalties in accordance with the course and university's academic integrity policy.
Student Responsibility
It is each student's responsibility to understand this policy and to use AI tools responsibly and ethically. Questions or doubts about the use of AI tools should be directed to Dr. Treviño.
Use AI for assignment preparation, ideation, and review.
Properly cite AI-generated content.
Avoid unethical use such as deception or plagiarism.
Engage critically with AI, verifying and adapting outputs. Be aware of the limitations and potential biases of AI outputs.
WINK (What I Need to Know)
WINK is an AI-supported academic assistant used in this course for organization, course questions, studying, project management, and academic planning. Access instructions will be provided in Blackboard and during the WINK introduction listed on the course calendar.
Use WINK as a learning partner: ask questions, clarify assignments, brainstorm, organize your work, review writing, plan study time, and explore course themes such as Identity, Agency, Belonging, and Aspirations.
Required Text
The Moth Presents: All These Wonders (edited by Catherine Burns)    DO NOT BUY
All These Wonders: True Stories About Facing the Unknown is a collection of 45 real-life stories from The Moth, a nonprofit dedicated to the art of storytelling. The book features a wide range of voices — from well-known names to everyday people — sharing personal experiences about taking risks, facing uncertainty, and discovering new perspectives. Edited by Catherine Burns, the anthology highlights the power of storytelling to connect, inspire, and reveal the extraordinary in the everyday.
Major Assignments & Points
Attendance
Attendance and participation are essential parts of this class. Students are expected to attend each class period and participate in discussions, group projects, and in-class activities. Students are allowed a total of two class absences for mental health. You MUST have a doctor's note to excuse an absence. Unless arrangements are made with the instructor, students will be dropped on the 3rd class absence. Two late arrivals over 10 minutes = 1 absence.
Common Read
Students will read The Moth Presents: All These Wonders: True Stories About Facing the Unknown, edited by Catherine Burns. It is up to the instructor to design the assignments/activities upon which the grades are based.
Entrepreneurial Mindset
EM is a set of characteristics, attitudes, behaviors, and skills that help students identify and make the most of opportunities, overcome and learn from setbacks, and succeed in a variety of settings. These characteristics, attitudes, behaviors, and skills drive action and are essential for navigating the college experience and preparing for life after graduation.
The domains of EM include:
Identity and self-awareness
Growth mindset
Critical thinking
Agency
Aspirations
Belonging
Becoming a Miner Group Project — the major course project
This 300-point team project is the most important assignment in the course. Your team will create a 10–15 minute video that tells the real story of the freshman experience at UTEP for future college students. You will build it in stages throughout the semester, with each deliverable aligned to the course themes of Identity, Agency, Belonging, and Aspirations. See the separate Fall 2026 Group Project Instructions for the complete requirements, checkpoints, peer-evaluation system, and showcase expectations.
Grading Scale
Course Policies — Written in Stone
No late work. Late work will not be graded unless approved with medical documentation. Deadlines are Sundays at 5:00 p.m.
Attendance is mandatory. Two late arrivals = 1 absence. Three absences will result in being dropped. Two mental health days are allowed.
Use AI tools on all written work and emails.
No phones/laptops during class lectures. Build your in-person collaboration skills.
Emails must include your CRN in the subject line.
Technology Requirements
Access to Blackboard Ultra, UTEP email, Microsoft Word or PDF tools, and Adobe Express.
Use a laptop or desktop computer for reliable access to assignments and uploads.
A web browser that supports Blackboard, YouTube, TED Talks, Yuja, Kanopy, and other forms of media as needed throughout the course.
Microsoft Office 365. Assignments must be submitted as a Word document (.doc or .docx) or PDF. Attachments in any other format will not be graded. You can download a free copy of Microsoft Office here.
Course Management System
Blackboard Ultra is the online course management system we will use throughout the semester. You can access Blackboard through my.utep.edu. In Blackboard, you can view the syllabus, course calendar, and other supplemental materials related to the course. You must check Blackboard daily for course announcements, assignments, and updates.
The Blackboard app is great for course announcements, emails, and discussions. However, a desktop or laptop computer is recommended for downloading and/or reading course materials, uploading documents, or submitting assignments. Call the Help Desk at 747-5257 if you need help with access. Should Blackboard go down for maintenance or other interruptions, email your instructor for assistance.
Course Policies
Absence Policy
Attendance is absolutely mandatory. You are expected to attend each class period and to participate in discussions, group projects, and in-class activities. You are allowed a total of two class absences for mental health. Unless arrangements are made with your instructor, you will be dropped on the 3rd class absence. Two late appearances equate to an absence.
Late Work
Absolutely no late work. Late work will not be graded. Deadlines are Sundays at 5:00 p.m.
Course Drop / Withdrawal
You may be dropped from this course if you exceed the required amount of absences and/or fail to keep current with assignments, unless arrangements are made with your instructor.
Syllabus Change
Except for changes that substantially affect the grading statement, this syllabus is a guide for the course and is subject to change. Any changes to the syllabus will be announced in class and/or on Blackboard. It is your responsibility to stay updated.
Grievances
If you have any concerns about the course, your grades, issues with other students, etc., please speak with your instructor. They are in the best position to help you.
If you have made a good-faith effort but have not been able to resolve the issue, your next step is to speak to Alejandro Mena, Associate Director of the Entering Student Experience (alemena@utep.edu, (915) 747-6532, UGLC 308).
If you have problems with registration, course documents, etc., please speak with UNIV 1301 Program Lead Sergio Contreras (scontreras@utep.edu, (915) 747-8444, UGLC 344).
University Policies
Accommodations
The Americans with Disabilities Act requires that reasonable accommodations be provided for students with disabilities. Please contact CASS at 747-5148, Union East 106, or cass@utep.edu.
Academic Integrity
Scholastic dishonesty is never tolerated by UTEP or by the Entering Student Experience. All suspected cases are reported to the Office of Student Conduct and Conflict Resolution (OSCCR) for review. For more information, click here.
Copyright and Fair Use
The University requires all members of its community to follow copyright and fair use requirements. Students are individually and solely responsible for violations of copyright and fair use laws. The university will neither protect nor defend students nor assume any responsibility for student violations. Violations of copyright laws could subject students to federal and state civil penalties and criminal liability, as well as disciplinary action under university policies.
Student Conduct
From the Handbook of Operating Procedures: Student Conduct and Discipline. Each student is responsible for notice of and compliance with the provisions of the Regents' Rules and Regulations, which are available here.
UTEP Financial Aid
1. Financial Aid Acknowledgement Requirement (Mandatory)
"Students receiving financial aid must complete the 'Financial Aid Acknowledgement Requirement' in Blackboard for each course within the first two weeks of the semester to confirm attendance and maintain full funding. Failure to do so may result in aid adjustments or cancellation."
2. Academic Progress & Degree Eligibility
"Financial aid eligibility requires enrollment in courses that apply toward your degree plan. Students taking courses for general improvement or specific certificates may not qualify."
"If you are a graduate student or taking prerequisite courses, you may need to complete the 'Statement of Academic Intent' and submit it with required documents (degree evaluation/prerequisite letter) to the Financial Aid Office."
3. Financial Aid Information & Resources
"For questions about your award, viewing aid, or payment, visit my.utep.edu > Goldmine > Financial Aid, or contact studentfinancialaid@utep.edu."
"Refer to the Office of Student Financial Aid for detailed policies on aid eligibility, cost of attendance, and other assistance."
4. Optional: Low-Cost Materials (If Applicable)
"This course is designated as low-cost. A detailed list of required materials will be provided during the first week of class."
By including these points, this syllabus covers federal requirements, university policies, and essential student actions, ensuring clarity for students.
Campus Resources
Academic Advising Center
Counseling and Psychological Services
Center for Accommodations and Support Services
Financial and Social Support Services (FSSS)
Food Pantry
Foster, Homeless, Adopted Resources (FHAR)
History Tutoring Center
Math Resource Center for Students (MaRCS)
Miner Learning Center
Student Financial Aid
Student Health & Wellness Center
Student Success Helpdesk
University Career Center
University Writing Center
UTEP Edge
UTEP Library
UTEP Police Department
Military Student Success Center
Miner Support
This syllabus is a living document and may be updated as needed. Changes will be announced via Blackboard and in class.
FALL 2026  •  YOUR COLLEGE EXPERIENCE STARTS HERE
CRN | Days / Time | Location
CRN #10248 | MWF 9:30–10:20 a.m. | UGLC 208
CRN #10196 | MWF 12:30–1:20 p.m. | Education 318
CRN #10247 | TT 7:30–8:50 a.m. | UGLC 334
Role | Course Section | Name | Email | Office Hours | Location
CRN #10248 | MWF 9:30 a.m. | Sam Vazquez | savasquez5@miners.utep.edu | MW 11:30 a.m.-12:30 p.m. | UGLC 304
CRN #10196 | MWF 12:30 a.m. | Emily Martinez | ermartinez9@miners.utep.edu | M 3 p.m. – 4 p.m.; T 9:00  a.m. -10:00 a.m.; W  11:30 a.m. – 12:30 p.m. | UGLC 304
CRN #10247 | TT 7:30 a.m. | Emily Martinez | ermartinez9@miners.utep.edu | By arrangement | UGLC 304
Librarian | Bob Klapthor | kklapthor@utep.edu
Academic Advisor | Jorge Carmargo | jcamargosalaz@utep.edu
Academic Advisor | Alexis Corona | acorona16@utep.edu
Learning Outcome
Students will demonstrate their ability to act ethically and responsibly for the benefit of society by articulating an awareness of social problems, exercising ethical leadership practices, engaging in civic, political, or community activities and/or advocating for social justice.
WINK supports your work; it does not replace your thinking or your responsibility for assignments. Verify AI-generated information and follow the course academic-integrity requirements.
Do not enter unnecessary sensitive or private information. Follow the WINK Terms of Agreement and any separate research consent materials that apply to you.
MAJOR ASSIGNMENTS & POINTS | PTS
CORE COURSE REQUIREMENTS | CORE COURSE REQUIREMENTS
Attendance | 100
Common Read Participation | 100
Entrepreneurial Mindset Activities | 100
Becoming a Miner Group Project | 300
DISCRETIONARY ITEMS | DISCRETIONARY ITEMS
Clifton Strengths | 25
Survivor Series | 100
Career Activity | 100
Peer Leader Group Meeting | 50
Choices 360 | 25
ESE Event | 100
TOTAL | 1000
Your College Story Matters!
What if you had the chance to go back in time and give your high school self a real, unfiltered look at what starting college is actually like? Would you warn yourself about the things no one tells you? Share the best parts of being a freshman? Show off the campus resources that helped you survive your first semester? Show the fun things to do? What would you say?
That is EXACTLY what you are going to do for your Freshman Capstone Project!
The Mission
Your team of three will create a fun, creative, and informative video (10–15 minutes) that tells the real story of your freshman experience at UTEP. This is not just another class project — your video will be shared with actual high school students to help them make decisions about going to college. This is your chance to pay it forward and show future college students what they can really expect — the good, the unexpected, and everything in between!
A | 900 – 1000
B | 800 – 899
C | 700 – 799
D | 600 – 699
F | 0 – 599"""),
        ("UNIV 1301", "10196", "UNIV_1301_Fall2026_CalendarMWF.docx", "course_calendar",
         """CRN #10248   MWF, 9:30 – 10:20 a.m.   ·   Undergraduate Learning Center 208
CRN #10196   MWF, 12:30 – 1:20 p.m. - Education 318
CRN #10247   TT, 7:30 – 8:50 a.m.   ·   Undergraduate Learning Center 334
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
✨ UTEP Edge Test | In-Class | Sunday, Aug. 30th, 5:00 p.m.
2 – IDENTITY | Mon. Aug. 31 | ChatGPT and Adobe Express Tutorials
Wed. Sep. 2 | 💻 ChatGPT / Adobe Express Tutorials (Bring laptop) | In-Class
3 – IDENTITY | Fri. Sep. 4 | 📅 Schedule ESE Event https://minetracker.utep.edu/events  You must attend one.

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
📅 Schedule ESE Event (minetracker.utep.edu/events?query=ESE)
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
11 – BELONGING | Mon Oct. 12 | 📚 Book Club #4 | UGLC | Book Club Essay Due Oct. 25, 5:00 p.m.
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
Fri. Nov. 6 | Clifton Strengths | Sunday, Nov. 8th, Group Project Review Due
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
    deadlines = [
        (1,"UNIV 1301","Clifton Strengths Assessment",today+timedelta(days=1),"confirmed",False),
        (2,"MATH 1324","Homework: Functions",today+timedelta(days=2),"confirmed",False),
        (3,"HIST 1301","Primary Source Analysis",today+timedelta(days=2),"confirmed",False),
        (4,"BIOL 1305","Chapter 4 Quiz",today+timedelta(days=3),"corrected",False),
        (1,"UNIV 1301","Survivor Series Activity",today+timedelta(days=5),"confirmed",False),
        (2,"MATH 1324","Exam 1",today+timedelta(days=6),"confirmed",False),
        (3,"HIST 1301","Reading Response 3",today-timedelta(days=3),"confirmed",True),
        (4,"BIOL 1305","Cell Lab Worksheet",today-timedelta(days=5),"confirmed",True),
        # Spread further out across the following couple of months — a real
        # semester's deadlines aren't clustered in the first two weeks, and
        # a demo that only ever seeds ~11 days of assignments left the
        # calendar (and the "Study Plan — Next 4 Weeks" feature) looking
        # empty the moment someone browsed past the current month.
        (1,"UNIV 1301","Common Read Reflection",today+timedelta(days=14),"confirmed",False),
        (2,"MATH 1324","Homework: Systems of Equations",today+timedelta(days=16),"confirmed",False),
        (3,"HIST 1301","Midterm Exam",today+timedelta(days=21),"confirmed",False),
        (4,"BIOL 1305","Genetics Lab Report",today+timedelta(days=24),"confirmed",False),
        (1,"UNIV 1301","Becoming a Miner Group Project — Checkpoint 1",today+timedelta(days=35),"confirmed",False),
        (2,"MATH 1324","Exam 2",today+timedelta(days=42),"confirmed",False),
        (3,"HIST 1301","Reading Response 4",today+timedelta(days=45),"confirmed",False),
        (4,"BIOL 1305","Final Project Draft",today+timedelta(days=56),"confirmed",False),
        (1,"UNIV 1301","Becoming a Miner Group Project — Final Video",today+timedelta(days=63),"confirmed",False),
        (2,"MATH 1324","Final Exam",today+timedelta(days=70),"confirmed",False),
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
        # Matches the Fall 2026 UNIV 1301 syllabus's "Major Assignments &
        # Points" table (1000 points total), converted to percentages —
        # the old 4-category breakdown here was from a prior semester's
        # syllabus and no longer matched the current course.
        "UNIV 1301":[("Attendance",10),("Common Read Participation",10),("Entrepreneurial Mindset Activities",10),
                     ("Becoming a Miner Group Project",30),("Clifton Strengths",2.5),("Survivor Series",10),
                     ("Career Activity",10),("Peer Leader Group Meeting",5),("Choices 360",2.5),("ESE Event",10)],
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
        {"role":"assistant","content":"You have a busy stretch coming up. Start with your UNIV 1301 Clifton Strengths assessment, then your MATH homework, and leave time to review for the biology quiz."}
    ])
    cur.execute("INSERT INTO conversations(student_id,title,messages,updated_at) VALUES(%s,%s,%s,NOW())",
                (sid,"Planning my week",messages))


@bp.route("/demo/start", methods=["POST"])
def start_demo():
    if not config.DB_URL:
        return "Demo mode requires the database.", 503
    # 5/hour previously — tightened to reduce the worst-case AI-cost
    # exposure from a single IP (5 sessions x the 25-call shared budget
    # in chat.py = up to 125 calls/hour before this change). Note this
    # is a meaningful reduction, not a complete fix — IP-based limits
    # are inherently weak against VPNs/proxies/distributed requests; a
    # sufficiently motivated abuser can still route around this. 3/hour
    # is still generous for a real visitor restarting a demo they
    # messed up, while cutting the single-IP worst case by 40%.
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
                       VALUES(%s,%s,'DemoWINK','Demo','Freshman','Business','University of Texas at El Paso','',TRUE,TRUE,TRUE,NOW() + %s * INTERVAL '1 hour') RETURNING id""",
                    (email,generate_password_hash(secrets.token_urlsafe(24)),DEMO_TTL_HOURS))
        sid=cur.fetchone()["id"]
        _seed_demo(cur,sid)
    session.clear(); session.permanent=False
    session["sid"]=sid; session["is_demo"]=True
    return redirect(url_for("documents.documents_page"))


@bp.route("/purge-expired-demos", methods=["POST"])
@csrf.exempt
def purge_expired_demos_cron():
    """Independently, reliably cleans up expired demo accounts on a
    schedule — previously this only happened opportunistically, when
    someone else started a NEW demo (_purge_expired() was called as a
    side effect of start_demo() above) or when an expired demo's own
    session happened to be accessed again. If neither of those things
    happened — a quiet period with no new demo visitors — an expired
    demo account could sit in the database indefinitely with no
    guaranteed cleanup. Meant to be called by an external scheduler,
    same pattern as /send-deadline-reminders, /send-weekly-digest, and
    /purge-deleted-conversations — same header-based auth, same run
    logging via cron_runs."""
    provided = request.headers.get("X-WINK-Cron-Secret", "")
    if not provided:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            provided = auth_header[len("Bearer "):]
    if not config.CRON_SECRET or not secrets.compare_digest(provided, config.CRON_SECRET):
        return jsonify({"error": "Not authorized"}), 403
    if not config.DB_URL:
        return jsonify({"error": "No database"}), 500

    with db_cursor(commit=True) as cur:
        cur.execute("INSERT INTO cron_runs(job_name) VALUES('purge_expired_demos') RETURNING id")
        run_id = cur.fetchone()["id"]

    try:
        with db_cursor(commit=True) as cur:
            cur.execute("SELECT id FROM students WHERE is_demo=TRUE AND demo_expires_at < NOW()")
            expired_count = len(cur.fetchall())
            _purge_expired(cur)

        with db_cursor(commit=True) as cur:
            cur.execute("UPDATE cron_runs SET completed_at=NOW(), number_processed=%s WHERE id=%s",
                        (expired_count, run_id))

        return jsonify({"purged": expired_count})
    except Exception as e:
        log_error("demo.purge_expired_demos_cron", e)
        try:
            with db_cursor(commit=True) as cur:
                cur.execute("UPDATE cron_runs SET completed_at=NOW(), last_error=%s WHERE id=%s",
                            (str(e)[:500], run_id))
        except Exception:
            pass
        return jsonify({"error": "Something went wrong on our end. Please try again."}), 500
