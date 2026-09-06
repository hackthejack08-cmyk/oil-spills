#!/usr/bin/env bash
# One-command launcher for Linux / macOS.  Usage:  ./run.sh          (first run creates .venv and installs deps)
#                                                   ./run.sh --port 9000
set -e; cd "$(dirname "$0")"
PORT=8000; [ "$1" = "--port" ] && PORT=$2
PY=${PYTHON:-python3}
if [ ! -d .venv ]; then
  echo "▶ creating virtualenv (.venv) and installing dependencies (≈ 2–4 min, ~1 GB incl. CPU torch)…"
  $PY -m venv .venv
  . .venv/bin/activate
  pip install -q --upgrade pip
  pip install -q torch torchvision --index-url https://download.pytorch.org/whl/cpu
  pip install -q -r backend/requirements.txt
else
  . .venv/bin/activate
fi
[ -f demo/data/synthetic_s1_scene.tif ] || python demo/make_demo_data.py
[ -f .env ] && set -a && . ./.env && set +a
echo "▶ Oil Spill Intelligence → http://localhost:$PORT   (Ctrl-C to stop)"
cd backend && exec python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
