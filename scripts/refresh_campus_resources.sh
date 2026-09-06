#!/usr/bin/env bash
# Triggers WINK's cached campus-resource contact info refresh
# (POST /refresh-campus-resources). Looks up current contact info for
# the handful of common offices (Financial Aid, Counseling, Advising,
# Writing Center, Tutoring, Career Center) per university with active
# students, via a real Anthropic + web_search call per office — so the
# chat itself can serve these from cache instead of searching live on
# every message that mentions one. Recommended cadence: weekly (see
# scripts/README.md) — this info changes rarely, so running it more
# often just spends extra API calls for no real freshness gain.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./_cron_common.sh
run_cron_endpoint "/refresh-campus-resources"
