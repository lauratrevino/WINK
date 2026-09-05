# Scheduled jobs

Four scripts here each trigger one scheduled WINK endpoint. None of them
run themselves — something external (a Render Cron Job, a system
crontab, GitHub Actions on a schedule, etc.) has to invoke each script on
the cadence below. Every endpoint is authenticated by the
`X-WINK-Cron-Secret` header (see `wink/services/cron.py`), so
`CRON_SECRET` must be set in whatever environment actually runs these
scripts — not just in the deployed app's environment.

| Script | Endpoint | Recommended schedule | Safe to run more often? |
|---|---|---|---|
| `send_deadline_reminders.sh` | `POST /send-deadline-reminders` | every few hours | Yes — each deadline is only emailed once (`reminded=TRUE` after a successful send). |
| `send_weekly_digest.sh` | `POST /send-weekly-digest` | once a week, Monday morning | Yes — the endpoint itself skips a duplicate send within 6 days. |
| `purge_deleted_conversations.sh` | `POST /purge-deleted-conversations` | once a day | Yes — idempotent; nothing to purge between runs beyond what newly crossed 3 months. |
| `purge_expired_demos.sh` | `POST /purge-expired-demos` | once a day (or more) | Yes — idempotent. |

## Configuration

Both variables are read from the environment the script runs in, not
from a config file:

- `CRON_SECRET` — required. Must match the `CRON_SECRET` set on the
  deployed WINK app.
- `WINK_BASE_URL` — optional, defaults to `https://mywink.ai`. Set this
  to point a script at a staging deployment or local dev server instead.

## Example: system crontab

```cron
# m h  dom mon dow   command
0 */4 * * *   CRON_SECRET=... /path/to/scripts/send_deadline_reminders.sh
0 8 * * 1     CRON_SECRET=... /path/to/scripts/send_weekly_digest.sh
0 3 * * *     CRON_SECRET=... /path/to/scripts/purge_deleted_conversations.sh
0 4 * * *     CRON_SECRET=... /path/to/scripts/purge_expired_demos.sh
```

Storing `CRON_SECRET` directly in a crontab line is fine for a
single-user box but visible to anyone who can read that file; prefer an
environment file loaded by the scheduler (e.g. Render Cron Job's own
environment variables panel) where available.

## Monitoring

Every run is recorded in the `cron_runs` table (start time, completion
time, counts, last error) regardless of which scheduler triggered it.
The admin health page (`/health-page`) surfaces each job's most recent
run and flags one that has never run or that last failed — see
`wink/services/health.py`'s `_check_cron_job`.
