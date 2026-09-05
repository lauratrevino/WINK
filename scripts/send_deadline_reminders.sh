#!/usr/bin/env bash
# Triggers WINK's 3-day-out deadline reminder emails
# (POST /send-deadline-reminders). The endpoint itself only emails each
# deadline once (it sets `reminded=TRUE` after a successful send), so
# calling this more often than the scheduled cadence is harmless — see
# scripts/README.md for the recommended schedule.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./_cron_common.sh
run_cron_endpoint "/send-deadline-reminders"
