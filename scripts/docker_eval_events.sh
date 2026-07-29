#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ $# -lt 1 ]]; then
  echo "Usage: scripts/docker_eval_events.sh logs/<run>_events.jsonl" >&2
  exit 1
fi

EVENTS_PATH="$1"
IMAGE_NAME="${IMAGE_NAME:-site-agent-eval:local}"
PROFILE="${PROFILE:-public_security_assistant}"
SUITE="${SUITE:-regression}"
LLM_ASSISTANT="${LLM_ASSISTANT:-1}"

if [[ ! -f "$EVENTS_PATH" ]]; then
  echo "Events file not found: $EVENTS_PATH" >&2
  exit 1
fi

env_args=()
if [[ -f ".env.docker" ]]; then
  env_args+=(--env-file ".env.docker")
fi
env_args+=(
  -e SITE_AGENT_EVAL_LLM_API_KEY
  -e SITE_AGENT_EVAL_LLM_MODEL
  -e SITE_AGENT_EVAL_LLM_BASE_URL
  -e SITE_AGENT_EVAL_LLM_VERIFY_SSL
  -e SITE_AGENT_EVAL_LLM_ASSISTANT
)

cmd=(python main.py --target "${PROFILE}.events.${SUITE}" --events "$EVENTS_PATH")
if [[ "$LLM_ASSISTANT" == "1" || "$LLM_ASSISTANT" == "true" ]]; then
  cmd+=(--llm-assistant)
fi

docker run --rm -it \
  "${env_args[@]}" \
  -v "$PWD:/app" \
  -w /app \
  "$IMAGE_NAME" \
  "${cmd[@]}"
