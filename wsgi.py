"""WSGI entry point.

* Production: ``gunicorn wsgi:app`` (see docker/entrypoint.sh)
* Development: ``flask --app wsgi run --debug`` or ``python wsgi.py``
"""

from __future__ import annotations

import os

from app import create_app

app = create_app()


if __name__ == "__main__":  # pragma: no cover - local development helper
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", 8000)),
        debug=bool(app.config.get("DEBUG")),
    )
