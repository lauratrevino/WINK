#!/usr/bin/env bash
# Shared setup for WINK's scheduled cron scripts — not meant to be run
# directly; each job script below sources this file.
#
# set -e: stop on the first failing command instead of continuing past it
#         (previously missing — a failed curl due to e.g. a bad response
#         code with -f would still exit 0 from the script's point of
#         view unless the scheduler happened to check curl's own exit
#         code, not the script's).
# set -u: treat an unset variable as an error rather than silently
#         expanding to an empty string.
# set -o pipefail: a failure earlier in a pipeline isn't masked by a
#         later command that succeeds.
set -euo pipefail

: "${CRON_SECRET:?CRON_SECRET must be set in the cron scheduler environment (the endpoint rejects every request without it)}"

# Configurable so the same scripts work against a staging deployment or
# local dev server without editing them — defaults to production.
WINK_BASE_URL="${WINK_BASE_URL:-https://mywink.ai}"

# Shared request helper: POSTs to a WINK cron endpoint with the header-
# based auth every one of these endpoints expects (see services/cron.py's
# is_authorized_cron_request()). `-sf` makes curl fail (non-zero exit,
# tripped by `set -e` above) on a non-2xx response instead of printing
# an HTML error page and exiting 0.
run_cron_endpoint() {
  local path="$1"
  curl -sf -X POST "${WINK_BASE_URL}${path}" \
    -H "X-WINK-Cron-Secret: ${CRON_SECRET}"
  echo
}
