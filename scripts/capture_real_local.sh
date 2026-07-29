#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PROFILE="${PROFILE:-public_security_assistant}"
SUITE="${SUITE:-regression}"
URL="${URL:-}"
PYTHON_BIN="${PYTHON_BIN:-./wch/bin/python}"
LLM_ASSISTANT="${LLM_ASSISTANT:-1}"
CAPTURE_ONLY="${CAPTURE_ONLY:-0}"

if [[ -z "$URL" ]]; then
  echo "URL is required. Example: URL=https://your-agent.example.com/path scripts/capture_real_local.sh" >&2
  exit 1
fi

cmd=(
  "$PYTHON_BIN" main.py
  --target "${PROFILE}.real.${SUITE}"
  --url "$URL"
)

if [[ "$LLM_ASSISTANT" == "1" || "$LLM_ASSISTANT" == "true" || "$LLM_ASSISTANT" == "yes" ]]; then
  export SITE_AGENT_EVAL_LLM_ASSISTANT=1
  cmd+=(--llm-assistant)
fi

if [[ "$CAPTURE_ONLY" == "1" || "$CAPTURE_ONLY" == "true" || "$CAPTURE_ONLY" == "yes" ]]; then
  cmd+=(--capture-only)
fi

"${cmd[@]}"
