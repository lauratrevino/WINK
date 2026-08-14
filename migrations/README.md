# Database Migrations

This project now uses [Alembic](https://alembic.sqlalchemy.org/) for schema
changes going forward. `wink/extensions.py`'s `init_db()` still runs on every
app startup exactly as before — that's intentional, existing deploys keep
working with zero extra steps. But **new** schema changes should go through
Alembic from now on, not new lines added to `init_db()`.

## One-time setup on your existing (already-running) database

Your production database already has every table `init_db()` builds — it's
been running for a while. Don't run `alembic upgrade head` against it; that
would try to `CREATE TABLE` things that already exist and fail. Instead, tell
Alembic "this database already matches the baseline" without re-running any
SQL:

```bash
DATABASE_URL="<your real DATABASE_URL>" alembic stamp head
```

Do this **once**, against your real production database. After that, Alembic
knows where things stand and future migrations layer on top correctly.

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
