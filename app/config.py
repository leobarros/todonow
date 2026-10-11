"""Application configuration.

Every setting can be overridden through environment variables so the same image
runs unchanged in development, docker-compose and the homelab.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _default_database_uri() -> str:
    """Prefer DATABASE_URL; otherwise keep a local SQLite file."""
    explicit = os.environ.get("DATABASE_URL")
    if explicit:
        return explicit
    data_dir = os.environ.get("DATA_DIR")
    if data_dir:
        return f"sqlite:///{Path(data_dir) / 'todonow.db'}"
    return f"sqlite:///{BASE_DIR / 'instance' / 'todonow.db'}"


class Config:
    """Base configuration shared by all environments."""

    # --- Core -----------------------------------------------------------
    APP_NAME = os.environ.get("APP_NAME", "todonow")
    # None means "not configured": the factory then persists a generated key
    # under the instance folder so sessions survive restarts.
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SECRET_KEY_FILE = os.environ.get("SECRET_KEY_FILE")
    ENV = os.environ.get("FLASK_ENV", "production")
    DEBUG = _bool("FLASK_DEBUG", False)
    TESTING = False

    # --- Locale ---------------------------------------------------------
    # All "today" computations use this timezone.
    TIMEZONE = os.environ.get("APP_TIMEZONE", "America/Sao_Paulo")
    LOCALE = os.environ.get("APP_LOCALE", "pt_BR")
    # First day of week: 0 = Monday ... 6 = Sunday
    WEEK_START = _int("WEEK_START", 6)

    # --- Database -------------------------------------------------------
    SQLALCHEMY_DATABASE_URI = _default_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        # SQLite is single-writer; keep a generous timeout instead of failing fast.
        "connect_args": {"timeout": 30, "check_same_thread": False},
    }

    # --- Security / sessions -------------------------------------------
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Set to true only when serving over HTTPS (e.g. behind a reverse proxy).
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_DURATION = 60 * 60 * 24 * 30  # 30 days
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 14  # 14 days
    MAX_CONTENT_LENGTH = _int("MAX_CONTENT_LENGTH", 16 * 1024 * 1024)  # 16 MB uploads
    WTF_CSRF_TIME_LIMIT = None

    # --- Auth policy ----------------------------------------------------
    # Self-service registration. "auto" = open only while the instance has no users.
    ALLOW_REGISTRATION = os.environ.get("ALLOW_REGISTRATION", "auto").strip().lower()
    # When true, the very first registered user is promoted to admin.
    FIRST_USER_IS_ADMIN = _bool("FIRST_USER_IS_ADMIN", True)
    MIN_PASSWORD_LENGTH = _int("MIN_PASSWORD_LENGTH", 8)

    # --- Backups --------------------------------------------------------
    BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", BASE_DIR / "instance" / "backups"))
    BACKUP_KEEP = _int("BACKUP_KEEP", 20)
    # Allow restoring a full instance backup (users + password hashes).
    ALLOW_FULL_RESTORE = _bool("ALLOW_FULL_RESTORE", True)

    # --- Acesso remoto / proxy ------------------------------------------
    # true quando o app fica atrás de um proxy que termina o TLS (Tailscale
    # Serve, Caddy, Nginx). Faz o Flask confiar em X-Forwarded-Proto/Host.
    TRUST_PROXY = _bool("TRUST_PROXY", False)
    TRUST_PROXY_HOPS = _int("TRUST_PROXY_HOPS", 1)
    # Hosts aceitos (protege contra Host header forjado). Vazio = aceita todos.
    _trusted = os.environ.get("TRUSTED_HOSTS", "").strip()
    TRUSTED_HOSTS = [h.strip() for h in _trusted.split(",") if h.strip()] or None

    # --- UI -------------------------------------------------------------
    TASKS_PER_PAGE = _int("TASKS_PER_PAGE", 50)
    DEFAULT_THEME = os.environ.get("DEFAULT_THEME", "light")

    @staticmethod
    def init_app(app) -> None:  # pragma: no cover - hook for subclasses
        Path(app.instance_path).mkdir(parents=True, exist_ok=True)


class DevelopmentConfig(Config):
    DEBUG = True


class TestingConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS: dict = {}
    ALLOW_REGISTRATION = "yes"
    SECRET_KEY = "testing-secret-key"


class ProductionConfig(Config):
    DEBUG = False


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name: str | None = None):
    """Return the config class for *name* (or inferred from the environment)."""
    if name is None:
        name = os.environ.get("FLASK_ENV", "production")
    return CONFIGS.get(name, ProductionConfig)
