#!/usr/bin/env bash
# Start ChargeFair locally without Docker.
#
#   ./start-local.sh
#
# Creates a virtual environment on first use, installs the dependencies and
# starts the development server on http://127.0.0.1:8000.
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv"
PORT="${PORT:-8000}"

if [ ! -d "$VENV" ]; then
  echo "→ Lege virtuelles Environment in $VENV an ..."
  python3 -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "→ Installiere Abhängigkeiten ..."
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

if [ ! -f .env ]; then
  echo "→ Erzeuge .env aus .env.example ..."
  cp .env.example .env
  SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
  if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' "s|^SECRET_KEY=.*|SECRET_KEY=$SECRET|" .env
  else
    sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$SECRET|" .env
  fi
fi

echo "→ Starte die Anwendung auf http://127.0.0.1:$PORT"
echo "  Beenden mit Strg+C"
PORT="$PORT" exec python run.py
