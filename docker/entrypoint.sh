#!/usr/bin/env bash
# Entrypoint for the ChargeFair container.
#   docker run ... chargefair:1.0            -> initialise database, then serve
#   docker run ... chargefair:1.0 worker     -> run the mail/reminder worker
#   docker run ... chargefair:1.0 shell      -> interactive Flask shell
set -euo pipefail

export CHARGEFAIR_DATA="${CHARGEFAIR_DATA:-/data}"
mkdir -p "${CHARGEFAIR_DATA}"

mode="${1:-web}"

bootstrap() {
  echo "[entrypoint] Datenbank wird vorbereitet (${CHARGEFAIR_DATA}) ..."
  python - <<'PY'
import os
from chargefair.app import create_app
from chargefair.seed import init_db, seed_demo_history, seed_demo_round

app = create_app()
init_db(app)

if os.environ.get("SEED_HISTORY", "1") not in {"0", "false", "no"}:
    with app.app_context():
        seed_demo_history()
        seed_demo_round()

with app.app_context():
    from chargefair.models import ChargingPoint, User
    print(f"[entrypoint] Benutzer: {User.query.count()}, Ladepunkte: {ChargingPoint.query.count()}")
PY
}

case "${mode}" in
  web)
    bootstrap
    echo "[entrypoint] Starte Gunicorn auf Port 8000 ..."
    exec gunicorn \
      --bind 0.0.0.0:8000 \
      --workers "${GUNICORN_WORKERS:-2}" \
      --threads "${GUNICORN_THREADS:-4}" \
      --timeout 120 \
      --access-logfile - \
      --error-logfile - \
      "chargefair.app:create_app()"
    ;;
  worker)
    echo "[entrypoint] Starte Mail- und Erinnerungs-Worker ..."
    exec python -m chargefair.mail_worker
    ;;
  shell)
    exec flask --app chargefair.app:create_app shell
    ;;
  *)
    exec "$@"
    ;;
esac
