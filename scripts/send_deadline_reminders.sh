#!/usr/bin/env bash
curl -sf -X POST https://mywink.ai/send-deadline-reminders \
  -H "X-WINK-Cron-Secret: $CRON_SECRET"
