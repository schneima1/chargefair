"""Development entry point.

    python run.py

Reads the configuration from the environment (see ``.env.example``) and starts
the Flask development server. In production the container runs Gunicorn via
``docker/entrypoint.sh``.
"""
from __future__ import annotations

import os

from chargefair.app import create_app
from chargefair.seed import init_db

app = create_app()


if __name__ == "__main__":
    with app.app_context():
        # Make sure a fresh checkout works without manual steps.
        init_db(app)

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    debug = os.environ.get("FLASK_DEBUG", "1") not in {"0", "false", "no"}
    app.run(host=host, port=port, debug=debug)
