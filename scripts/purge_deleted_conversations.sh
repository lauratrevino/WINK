#!/usr/bin/env bash
# Hard-deletes conversations a student soft-deleted more than 3 months
# ago (POST /purge-deleted-conversations). Safe to run daily; there is
# nothing left to purge between runs beyond whatever newly crossed the
# 3-month mark.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./_cron_common.sh
run_cron_endpoint "/purge-deleted-conversations"
