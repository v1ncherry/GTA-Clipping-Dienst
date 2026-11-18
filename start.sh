#!/usr/bin/env bash
# GTA Clipper starten (Mac/Linux). Beim ersten Mal wird alles installiert.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Erstinstallation ..."
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then pip install mlx-whisper; fi
else
  source .venv/bin/activate
fi
[ -f config.yaml ] || python -m clipper setup
exec python -m clipper "$@"
