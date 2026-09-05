#!/usr/bin/env bash
# Ends any demo account whose demo_expires_at has passed
# (POST /purge-expired-demos). This also happens opportunistically
# whenever a new demo starts, but a scheduled run guarantees it happens
# even during a quiet period with no new demo visitors.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./_cron_common.sh
run_cron_endpoint "/purge-expired-demos"
