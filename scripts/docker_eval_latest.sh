#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

latest_events="$(find logs -maxdepth 1 -name '*_events.jsonl' -type f -print0 2>/dev/null | xargs -0 ls -t 2>/dev/null | head -n 1 || true)"
if [[ -z "$latest_events" ]]; then
  echo "No logs/*_events.jsonl file found." >&2
  exit 1
fi

echo "Using latest events file: $latest_events"
exec scripts/docker_eval_events.sh "$latest_events"
