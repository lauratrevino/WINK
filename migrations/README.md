# Database Migrations

This project now uses [Alembic](https://alembic.sqlalchemy.org/) for schema
changes going forward. `wink/extensions.py`'s `init_db()` still runs on every
app startup exactly as before — that's intentional, existing deploys keep
working with zero extra steps. But **new** schema changes should go through
Alembic from now on, not new lines added to `init_db()`.

## One-time setup on your existing (already-running) database

**Read this whole section before running anything — which command is
safe depends on which revision your production database is already at,
and guessing wrong can mark a migration "applied" without its SQL ever
having run.**

`head` is not a fixed point — it moves every time a new migration file
is added (there have been several since Alembic was introduced here:
timezone, first_generation, MFA columns, retrieved_context,
unverified_citations, a fulltext index, demo_sessions.student_id, and
university_other_name, in that order). Blindly stamping "head" only
does the right thing at the exact moment nothing new has been added
since your database was last confirmed current — otherwise it marks
every migration between where you actually are and head as "already
applied" without running a single one of them.

**Always check first, never guess:**

```bash
DATABASE_URL="<your real DATABASE_URL>" alembic current
```

- **Prints nothing at all** — this database has never been stamped, and
  (only if you're certain no migration's SQL has ever been run against
  it by hand either) it should match exactly what `init_db()` builds,
  no more. Stamp the **baseline**, not head:
  ```bash
  DATABASE_URL="<your real DATABASE_URL>" alembic stamp a0205eeb64e6
  DATABASE_URL="<your real DATABASE_URL>" alembic upgrade head
  ```
  The `upgrade head` step here is doing real work — it applies every
  migration since baseline, including columns that may not exist in
  your database yet. Every migration in this project uses `ADD COLUMN
  IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`, so this is safe to run
  even if some of those columns already exist from an earlier manual
  change.
- **Prints an actual revision id** — this database has already been
  stamped (or migrated) before. Do **not** stamp anything again; just
  bring it up to date:
  ```bash
  DATABASE_URL="<your real DATABASE_URL>" alembic upgrade head
  ```

Either way, confirm the result:

```bash
DATABASE_URL="<your real DATABASE_URL>" alembic current
```

It should now print the latest revision id in `migrations/versions/`
(check with `alembic history` if you're not sure which one that is).

## Setting up a brand new database (fresh dev environment, new deploy target)

Run the migrations from scratch — this builds the exact same schema
`init_db()` does (verified byte-for-byte: every column, type, default,
nullability, and index, checked against a real database built by `init_db()`
itself, not just reviewed by eye):

```bash
DATABASE_URL="<your DATABASE_URL>" alembic upgrade head
```

## Making a future schema change

1. Write the migration:
   ```bash
   DATABASE_URL="<your DATABASE_URL>" alembic revision -m "short description of the change"
   ```
   This creates a new file in `migrations/versions/`. Open it and fill in
   `upgrade()` (what the change does) and `downgrade()` (how to undo it) —
   use `op.execute("...")` with raw SQL, matching the style in the baseline
   migration. This app has no ORM models to autogenerate from, so
   `--autogenerate` won't work here — write the SQL by hand.

2. Test it locally first, against your dev database:
   ```bash
   DATABASE_URL="<your dev DATABASE_URL>" alembic upgrade head
   ```

3. Once you're confident it's right, apply it to production the same way:
   ```bash
   DATABASE_URL="<your real DATABASE_URL>" alembic upgrade head
   ```

## If a change goes wrong

Undo just the most recent migration, without touching any data from before
it or any real activity that happened after it:

```bash
DATABASE_URL="<your real DATABASE_URL>" alembic downgrade -1
```

This is the actual point of setting this up: Render's built-in Point-in-Time
Recovery can restore your *entire* database to an earlier moment, but that
throws away every real student interaction since that moment along with the
mistake you're trying to undo. `alembic downgrade -1` undoes *just* the
schema change, leaving everything else untouched.

## Checking where things stand

```bash
DATABASE_URL="<your DATABASE_URL>" alembic current    # what migration is this database on?
DATABASE_URL="<your DATABASE_URL>" alembic history    # every migration, in order
```
