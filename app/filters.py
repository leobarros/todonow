"""Jinja filters and global template helpers."""

from __future__ import annotations

import json

from markupsafe import Markup, escape

from . import utils


def register_filters(app) -> None:
    app.jinja_env.filters["date_pt"] = utils.format_date_pt
    app.jinja_env.filters["date_long_pt"] = utils.format_date_long_pt
    app.jinja_env.filters["date_short_pt"] = utils.format_date_short_pt
    app.jinja_env.filters["relative"] = utils.humanize_relative
    app.jinja_env.filters["month_pt"] = utils.month_label_pt
    app.jinja_env.filters["tojson_pretty"] = lambda value: json.dumps(value, ensure_ascii=False, indent=2)
    app.jinja_env.filters["nl2br"] = _nl2br


def _nl2br(value) -> Markup:
    """Escape text then convert newlines into ``<br>`` (safe for Jinja)."""
    if not value:
        return Markup("")
    return Markup("<br>".join(escape(str(value)).splitlines()))


def register_context(app) -> None:
    from datetime import date, timedelta

    from . import utils
    from .extensions import db  # noqa: F401  (ensures metadata is loaded)
    from .models import Task

    # Shared vocabulary. Exposed as Jinja *globals* (not only as context) so the
    # imported macros in ``_macros.html`` can use them without ``with context``.
    shared = {
        "APP_NAME": app.config["APP_NAME"],
        "date": date,
        "timedelta": timedelta,
        "TASK_KINDS": utils.TASK_KINDS,
        "KIND_LABELS": utils.KIND_LABELS,
        "KIND_SYMBOLS": utils.KIND_SYMBOLS,
        "KIND_ICONS": utils.KIND_ICONS,
        "TASK_STATUSES": utils.TASK_STATUSES,
        "STATUS_LABELS": utils.STATUS_LABELS,
        "STATUS_SYMBOLS": utils.STATUS_SYMBOLS,
        "TASK_PRIORITIES": utils.TASK_PRIORITIES,
        "PRIORITY_LABELS": utils.PRIORITY_LABELS,
        "PRIORITY_SIGNIFIERS": utils.PRIORITY_SIGNIFIERS,
        "PRIORITY_BADGES": utils.PRIORITY_BADGES,
        "today": utils.today_local,
        "now": utils.now_local,
        # weekday_labels follows WEEK_START (calendar headers);
        # WEEKDAYS_SHORT_PT/LONG_PT are Monday-indexed (Python weekday()).
        "weekday_labels": utils.weekday_labels_pt(app.config["WEEK_START"]),
        "WEEKDAYS_SHORT_PT": utils.WEEKDAY_SHORT_PT,
        "WEEKDAYS_LONG_PT": utils.WEEKDAY_LONG_PT,
        "WEEK_START": app.config["WEEK_START"],
        "Task": Task,
    }
    app.jinja_env.globals.update(shared)

    @app.context_processor
    def inject_globals():
        return dict(shared)
