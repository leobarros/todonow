"""Task blueprint: CRUD, completion, migration and bulk actions."""

from flask import Blueprint

bp = Blueprint("tasks", __name__, template_folder="../templates/tasks")

from . import routes  # noqa: E402,F401
