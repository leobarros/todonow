"""Main blueprint: dashboard, BUJO logs and calendar."""

from flask import Blueprint

bp = Blueprint("main", __name__)

from . import routes  # noqa: E402,F401
