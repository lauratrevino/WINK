#!/usr/bin/env bash
# Triggers WINK's once-a-week "here's your week ahead" digest email
# (POST /send-weekly-digest). Intended to run once, at the start of the
# week (e.g. Monday morning) — the endpoint itself also guards against a
# duplicate send within the same 6 days, so an accidental double-fire of
# the scheduler is harmless, not just a redundant email.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./_cron_common.sh
run_cron_endpoint "/send-weekly-digest"
