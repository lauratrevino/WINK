"""
One-off cleanup: remove leftover data tagged with a course name that no
document currently references (e.g. the "ijkh" entry showing on Progress).

Run once from the Render shell (or anywhere DATABASE_URL is set):

    python purge_orphaned_course.py --course ijkh --email you@utep.edu
    python purge_orphaned_course.py --course ijkh          # all students

--email scopes it to one account. Omit it to check/clean every student
(safe either way: it only deletes rows for students where NO document
still has that course name).
"""
import argparse
import os

import psycopg2
from psycopg2.extras import RealDictCursor


def normalize(name):
    return (name or "").strip().lower()


def purge(cur, student_id, course_norm, dry_run):
    cur.execute(
        "SELECT 1 FROM documents WHERE student_id=%s AND lower(trim(course))=%s LIMIT 1",
        (student_id, course_norm),
    )
    if cur.fetchone():
        print(f"  student {student_id}: a document still uses this course — skipped")
        return

    cur.execute(
        "SELECT COUNT(*) AS n FROM deadlines WHERE student_id=%s AND lower(trim(course))=%s AND is_personal IS NOT TRUE",
        (student_id, course_norm),
    )
    n_deadlines = cur.fetchone()["n"]
    cur.execute(
        "SELECT COUNT(*) AS n FROM practice_questions WHERE student_id=%s AND lower(trim(course))=%s",
        (student_id, course_norm),
    )
    n_practice = cur.fetchone()["n"]
    cur.execute(
        "SELECT COUNT(*) AS n FROM course_colors WHERE student_id=%s AND course_normalized=%s",
        (student_id, course_norm),
    )
    n_color = cur.fetchone()["n"]

    print(f"  student {student_id}: {n_deadlines} deadlines, {n_practice} practice questions, "
          f"{n_color} color entries")

    if dry_run or (n_deadlines == 0 and n_practice == 0 and n_color == 0):
        return

    cur.execute(
        "DELETE FROM deadlines WHERE student_id=%s AND lower(trim(course))=%s AND is_personal IS NOT TRUE",
        (student_id, course_norm),
    )
    cur.execute(
        "DELETE FROM practice_questions WHERE student_id=%s AND lower(trim(course))=%s",
        (student_id, course_norm),
    )
    cur.execute(
        "DELETE FROM course_colors WHERE student_id=%s AND course_normalized=%s",
        (student_id, course_norm),
    )
    print(f"  student {student_id}: purged")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", required=True, help="The course name to remove, e.g. ijkh")
    ap.add_argument("--email", help="Limit to a single student's account")
    ap.add_argument("--dry-run", action="store_true", help="Show counts without deleting")
    args = ap.parse_args()

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise SystemExit("DATABASE_URL is not set in this environment.")

    course_norm = normalize(args.course)
    conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            if args.email:
                cur.execute("SELECT id FROM students WHERE lower(email)=lower(%s)", (args.email,))
                row = cur.fetchone()
                if not row:
                    raise SystemExit(f"No student found with email {args.email}")
                student_ids = [row["id"]]
            else:
                cur.execute("""
                    SELECT DISTINCT student_id FROM (
                        SELECT student_id FROM deadlines WHERE lower(trim(course))=%s
                        UNION
                        SELECT student_id FROM practice_questions WHERE lower(trim(course))=%s
                        UNION
                        SELECT student_id FROM course_colors WHERE course_normalized=%s
                    ) x
                """, (course_norm, course_norm, course_norm))
                student_ids = [r["student_id"] for r in cur.fetchall()]

            if not student_ids:
                print(f'No data found anywhere tagged with course "{args.course}".')
                return

            print(f'Checking course "{args.course}" for {len(student_ids)} student(s)'
                  f'{" (dry run)" if args.dry_run else ""}...')
            for sid in student_ids:
                purge(cur, sid, course_norm, args.dry_run)

        if args.dry_run:
            conn.rollback()
            print("Dry run — no changes committed.")
        else:
            conn.commit()
            print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
