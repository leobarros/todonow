"""Backup blueprint: export, import and server-side snapshots."""

from flask import Blueprint

bp = Blueprint("backup", __name__, template_folder="../templates/backup")

from . import routes  # noqa: E402,F401
