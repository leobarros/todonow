"""Dashboard, BUJO daily/monthly/future logs and the calendar feed."""

from __future__ import annotations

from collections import OrderedDict
from datetime import date, timedelta

from flask import (
    abort,
    jsonify,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Task
from ..utils import (
    STATUS_DONE,
    STATUS_OPEN,
    add_months,
    format_date_long_pt,
    month_bounds,
    month_label_pt,
    parse_date,
    today_local,
)
from . import bp

OVERDUE_LIMIT = 200


def _day_param(name: str = "date", default: date | None = None) -> date:
    raw = request.args.get(name)
    return parse_date(raw, default or today_local())


@bp.get("/")
@login_required
def dashboard():
    from ..tasks.forms import QuickTaskForm

    today = today_local()
    task_form = QuickTaskForm(scheduled_date=today)
    task_form.scheduled_date.data = today

    today_tasks = Task.scheduled_on(current_user, today).all()
    overdue = Task.pending_before(current_user, today).limit(OVERDUE_LIMIT).all()
    completed_today = Task.completed_on(current_user, today).all()
    upcoming = Task.upcoming(current_user, today, until=today + timedelta(days=7)).all()

    done_today_ids = {task.id for task in today_tasks if task.is_done}
    open_today = [t for t in today_tasks if t.is_open]
    done_today = [t for t in today_tasks if t.status != STATUS_OPEN]
    for task in completed_today:
        if task.id not in done_today_ids:
            done_today.append(task)

    return render_template(
        "main/dashboard.html",
        today_label=format_date_long_pt(today),
        open_today=open_today,
        done_today=done_today,
        overdue=overdue,
        upcoming=upcoming,
        task_form=task_form,
        stats=Task.stats(current_user),
        collections=Task.collections(current_user),
    )


# --------------------------------------------------------------------------
# BUJO logs
# --------------------------------------------------------------------------


@bp.get("/day")
@bp.get("/day/<day_iso>")
@login_required
def daily_log(day_iso: str | None = None):
    from ..tasks.forms import QuickTaskForm

    day = parse_date(day_iso, _day_param("date"))
    task_form = QuickTaskForm()
    task_form.scheduled_date.data = day

    entries = Task.scheduled_on(current_user, day).all()
    carried = Task.pending_before(current_user, day).all()
    completed = Task.completed_on(current_user, day).all()
    collections = OrderedDict()
    for task in entries:
        collections.setdefault(task.collection or "Sem coleção", []).append(task)

    return render_template(
        "main/daily_log.html",
        day=day,
        day_label=format_date_long_pt(day),
        prev_day=day - timedelta(days=1),
        next_day=day + timedelta(days=1),
        entries=entries,
        carried=carried,
        completed=completed,
        collections=collections,
        task_form=task_form,
        stats=Task.stats(current_user),
    )


@bp.get("/monthly")
@bp.get("/monthly/<int:year>/<int:month>")
@login_required
def monthly_log(year: int | None = None, month: int | None = None):
    today = today_local()
    year = year or today.year
    month = month or today.month
    if not 1 <= month <= 12:
        abort(404)

    first, last = month_bounds(year, month)
    tasks = Task.between(current_user, first, last).all()
    by_day: dict[date, list[Task]] = {}
    for task in tasks:
        by_day.setdefault(task.scheduled_date, []).append(task)

    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    previous_month = add_months(first, -1)
    next_month = add_months(first, 1)
    return render_template(
        "main/monthly_log.html",
        year=year,
        month=month,
        month_label=month_label_pt(year, month),
        days=days,
        by_day=by_day,
        first=first,
        last=last,
        prev_year=previous_month.year,
        prev_month=previous_month.month,
        next_year=next_month.year,
        next_month=next_month.month,
        tasks=tasks,
        stats=_scope_stats(tasks),
        current_day=today,
    )


@bp.get("/future")
@login_required
def future_log():
    from ..tasks.forms import QuickTaskForm

    today = today_local()
    task_form = QuickTaskForm()
    task_form.scheduled_date.data = today + timedelta(days=1)
    tasks = Task.upcoming(current_user, today).all()
    groups: OrderedDict[str, list[Task]] = OrderedDict()
    for task in tasks:
        key = month_label_pt(task.scheduled_date.year, task.scheduled_date.month)
        groups.setdefault(key, []).append(task)

    return render_template(
        "main/future_log.html",
        groups=groups,
        total=len(tasks),
        task_form=task_form,
        stats=Task.stats(current_user),
    )


def _scope_stats(tasks: list[Task]) -> dict:
    total = len(tasks)
    done = sum(1 for t in tasks if t.status == STATUS_DONE)
    open_count = sum(1 for t in tasks if t.status == STATUS_OPEN)
    return {
        "total": total,
        "done": done,
        "open": open_count,
        "completion_rate": round(done / total * 100) if total else 0,
    }


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------


@bp.get("/calendar")
@login_required
def calendar():
    from ..tasks.forms import QuickTaskForm

    today = today_local()
    task_form = QuickTaskForm()
    task_form.scheduled_date.data = today
    return render_template(
        "main/calendar.html",
        task_form=task_form,
        stats=Task.stats(current_user),
        collections=Task.collections(current_user),
    )


@bp.get("/calendar/feed")
@login_required
def calendar_feed():
    """FullCalendar JSON feed.

    Accepts FullCalendar's ``start``/``end`` (ISO datetimes) plus optional
    ``status``/``kind``/``collection`` filters.
    """
    today = today_local()
    start = parse_date(request.args.get("start"), add_months(today, -3))
    end = parse_date(request.args.get("end"), add_months(today, 6))

    query = Task.for_user(current_user).filter(Task.scheduled_date >= start, Task.scheduled_date <= end)

    status = (request.args.get("status") or "").strip()
    kind = (request.args.get("kind") or "").strip()
    collection = (request.args.get("collection") or "").strip()
    if status:
        query = query.filter(Task.status == status)
    if kind:
        query = query.filter(Task.kind == kind)
    if collection:
        query = query.filter(Task.collection == collection)

    show_due = request.args.get("show_due", "1") not in {"0", "false", "no"}
    events: list[dict] = []
    for task in query.order_by(Task.scheduled_date, Task.position, Task.id).all():
        events.append(_event_payload(task, today))
        if show_due and task.due_date and task.due_date != task.scheduled_date:
            events.append(_event_payload(task, today, as_due=True))

    return jsonify(events)


def _event_payload(task: Task, today: date, as_due: bool = False) -> dict:
    start = task.due_date if as_due else task.scheduled_date
    classes = ["bujo-entry", f"bujo-{task.kind}", f"bujo-status-{task.status}"]
    if task.priority in {"medium", "high"}:
        classes.append(f"bujo-priority-{task.priority}")
    if as_due:
        classes.append("bujo-due")
    if task.is_open and start < today:
        classes.append("bujo-overdue")

    title = f"{task.bujo_symbol} {task.title}"
    if as_due:
        title = f"⚑ prazo: {task.title}"

    return {
        "id": f"due-{task.id}" if as_due else str(task.id),
        "taskId": task.id,
        "title": title,
        "start": start.isoformat(),
        "allDay": True,
        "classNames": classes,
        "url": url_for("tasks.detail", task_id=task.id),
        "editable": bool(task.is_open and not as_due),
        "extendedProps": {
            "kind": task.kind,
            "status": task.status,
            "priority": task.priority,
            "collection": task.collection,
            "notes": task.notes,
            "isDue": as_due,
            "symbol": task.bujo_symbol,
        },
    }


@bp.post("/calendar/move")
@login_required
def calendar_move():
    """Persist a drag & drop reschedule coming from FullCalendar."""
    payload = request.get_json(silent=True) or request.form
    task_id = payload.get("id") or payload.get("task_id")
    target = parse_date(payload.get("date") or payload.get("start"))
    try:
        task_id = int(task_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Tarefa inválida."}), 400
    if target is None:
        return jsonify({"ok": False, "error": "Data inválida."}), 400

    task = db.session.get(Task, task_id)
    if task is None or task.user_id != current_user.id:
        return jsonify({"ok": False, "error": "Tarefa não encontrada."}), 404

    task.scheduled_date = target
    if task.due_date and task.due_date < target:
        task.due_date = target
    db.session.commit()
    return jsonify({"ok": True, "task": task.to_dict(), "id": task.id})


@bp.post("/theme")
def set_theme():
    """Toggle/persist the Bootstrap colour mode (works for anonymous users too)."""
    payload = request.get_json(silent=True) or request.form
    theme = (payload.get("theme") or "").strip().lower()
    if theme not in {"light", "dark", "auto"}:
        theme = "dark"
    if current_user.is_authenticated:
        current_user.theme = theme
        db.session.commit()
    return jsonify({"ok": True, "theme": theme})
