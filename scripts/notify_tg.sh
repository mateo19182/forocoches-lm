#!/usr/bin/env bash
# Send a Telegram message, reusing the bot + chat id of services/tg-summary (.env is never printed).
#   scripts/notify_tg.sh "text"
set -euo pipefail
ENV_FILE="${TG_ENV_FILE:-$HOME/projects/services/tg-summary/.env}"
TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2- | tr -d '"'"'")
CHAT=$(grep -E '^TELEGRAM_CHAT_ID=' "$ENV_FILE" | cut -d= -f2- | tr -d '"'"'")
[[ -n "$TOKEN" && -n "$CHAT" ]] || { echo "notify_tg: token or chat id missing in $ENV_FILE" >&2; exit 1; }
curl -fsS -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${CHAT}" --data-urlencode "text=$1" >/dev/null
