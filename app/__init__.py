"""todonow — a local, BUJO-flavoured task manager for a homelab.

Application factory: ``create_app()`` wires configuration, extensions,
blueprints, CLI commands and error handling together.
"""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
from pathlib import Path

from flask import Flask, flash, jsonify, redirect, render_template, request
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.exceptions import HTTPException

from .config import get_config
from .extensions import csrf, db, login_manager, migrate
from .filters import register_context, register_filters

__version__ = "1.0.0"

logger = logging.getLogger(__name__)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record) -> None:
    """Tune SQLite for a multi-worker container: WAL + generous busy timeout.

    Without WAL, concurrent gunicorn workers would hit "database is locked"
    errors as soon as two people tick a checkbox at the same time.
    """
    if not dbapi_connection.__class__.__module__.startswith("sqlite3"):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA temp_store=MEMORY")
    finally:
        cursor.close()


def _resolve_instance_path() -> str | None:
    data_dir = os.environ.get("DATA_DIR")
    if data_dir:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        return str(Path(data_dir).resolve())
    return None


def _load_secret_key(app: Flask) -> None:
    """Use SECRET_KEY when provided, otherwise persist a generated one."""
    if app.config.get("SECRET_KEY"):
        return

    if app.config.get("TESTING"):
        app.config["SECRET_KEY"] = "testing-secret-key"
        return

    configured = app.config.get("SECRET_KEY_FILE")
    path = Path(configured) if configured else Path(app.instance_path) / "secret_key"
    try:
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                app.config["SECRET_KEY"] = value
                return
        value = secrets.token_hex(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        # Pode falhar em volumes montados sem suporte a chmod.
        with contextlib.suppress(OSError):
            path.chmod(0o600)
        app.config["SECRET_KEY"] = value
        logger.info("Generated a new SECRET_KEY at %s", path)
    except OSError:
        logger.warning("Could not persist SECRET_KEY; sessions will reset on restart")
        app.config["SECRET_KEY"] = secrets.token_hex(32)


def _ensure_sqlite_dir(app: Flask) -> None:
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite:///"):
        return
    db_path = uri.replace("sqlite:///", "", 1)
    if not db_path or db_path == ":memory:":
        return
    Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)


def create_app(config_name: str | None = None, **overrides) -> Flask:
    instance_path = _resolve_instance_path()
    app = Flask(
        __name__,
        instance_path=instance_path,
        instance_relative_config=True,
        template_folder="templates",
        static_folder="static",
    )

    app.config.from_object(get_config(config_name))
    if overrides:
        app.config.update(overrides)

    app.config.setdefault("APP_VERSION", __version__)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    _load_secret_key(app)
    _ensure_sqlite_dir(app)
    Path(app.config["BACKUP_DIR"]).mkdir(parents=True, exist_ok=True)

    # --- extensions ----------------------------------------------------
    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)
    login_manager.init_app(app)
    csrf.init_app(app)

    from .models import User

    @login_manager.user_loader
    def load_user(user_id: str):  # pragma: no cover - exercised via requests
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    # --- blueprints ----------------------------------------------------
    from .auth import bp as auth_bp
    from .backup import bp as backup_bp
    from .main import bp as main_bp
    from .tasks import bp as tasks_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(tasks_bp, url_prefix="/tasks")
    app.register_blueprint(backup_bp, url_prefix="/backup")

    # --- templating / cli ---------------------------------------------
    register_filters(app)
    register_context(app)

    from .cli import register_cli

    register_cli(app)

    # --- error handling / headers -------------------------------------
    _register_error_handlers(app)
    _register_security_headers(app)
    _register_health(app)

    app.logger.setLevel(logging.INFO)
    return app


def _wants_json() -> bool:
    if request.path.startswith("/api/") or request.path.startswith("/calendar/feed"):
        return True
    return request.accept_mimetypes.best == "application/json"


def _register_error_handlers(app: Flask) -> None:
    from flask_login import current_user
    from flask_wtf.csrf import CSRFError

    from .security import safe_referrer_path

    @app.errorhandler(CSRFError)
    def csrf_expired(error):
        """Formulário enviado com token expirado ou ausente.

        Em vez da página de erro 400, avisa e devolve o usuário para a tela onde
        ele estava — o caso mais comum é a aba ter ficado aberta por muito tempo.
        """
        db.session.rollback()
        message = (
            "Sua sessão expirou ou o formulário ficou aberto por muito tempo. " "Recarregue a página e tente novamente."
        )
        if _wants_json():
            return jsonify({"error": message, "status": 400}), 400
        flash(message, "warning")
        fallback = "main.dashboard" if current_user.is_authenticated else "auth.login"
        return redirect(safe_referrer_path(fallback))

    @app.errorhandler(400)
    def bad_request(error):
        return _render_error(400, "Requisição inválida", error)

    @app.errorhandler(403)
    def forbidden(error):
        return _render_error(403, "Acesso negado", error)

    @app.errorhandler(404)
    def not_found(error):
        return _render_error(404, "Página não encontrada", error)

    @app.errorhandler(413)
    def too_large(error):
        return _render_error(413, "Arquivo muito grande", error)

    @app.errorhandler(500)
    def server_error(error):  # pragma: no cover - hard to trigger on purpose
        db.session.rollback()
        app.logger.exception("Unhandled error on %s", request.path)
        return _render_error(500, "Erro interno", error)

    @app.errorhandler(HTTPException)
    def http_exception(error):  # pragma: no cover - fallback branch
        return _render_error(error.code or 500, error.name, error)


def _render_error(status: int, title: str, error):
    if _wants_json():
        return jsonify({"error": title, "status": status}), status
    return (
        render_template("errors/error.html", status=status, title=title, detail=getattr(error, "description", "")),
        status,
    )


def _register_security_headers(app: Flask) -> None:
    @app.after_request
    def set_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'self'",
        )
        return response


def _register_health(app: Flask) -> None:
    @app.get("/healthz")
    def healthz():
        try:
            from sqlalchemy import text

            db.session.execute(text("SELECT 1"))
            db_ok = True
        except Exception:  # pragma: no cover - only when the DB is unreachable
            app.logger.exception("Health check failed")
            db_ok = False
        payload = {"status": "ok" if db_ok else "degraded", "version": app.config["APP_VERSION"], "database": db_ok}
        return jsonify(payload), (200 if db_ok else 503)
