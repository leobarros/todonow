"""Authentication blueprint: local login, registration and profile."""

from flask import Blueprint

bp = Blueprint("auth", __name__, template_folder="../templates/auth")

from . import routes  # noqa: E402,F401  (imported for side effects)
