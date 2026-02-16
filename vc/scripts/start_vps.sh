#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${VOICECHAT_ROOM_TOKEN:-}" ]]; then
  echo "VOICECHAT_ROOM_TOKEN is not set"
  exit 1
fi

source .venv/bin/activate
python -m voicechat.server --host 0.0.0.0 --ws-port 8765 --udp-port 9999
