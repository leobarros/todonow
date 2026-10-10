"""Task CRUD, completion, migration and bulk-action routes."""

from __future__ import annotations

import contextlib
from datetime import timedelta

from flask import (
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Task
from ..utils import (
    KIND_TASK,
    PRIORITY_NONE,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_OPEN,
    TASK_KINDS,
    TASK_PRIORITIES,
    TASK_STATUSES,
    parse_date,
    today_local,
)
from . import bp
from .forms import FilterForm, QuickTaskForm, TaskForm

BULK_ACTIONS = {
    "complete": "Concluir selecionadas",
    "reopen": "Reabrir selecionadas",
    "cancel": "Cancelar selecionadas",
    "migrate_tomorrow": "Migrar para amanhã",
    "migrate_week": "Migrar para a próxima semana",
    "delete": "Excluir selecionadas",
}


def _wants_json() -> bool:
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.is_json
        or request.path.startswith("/api/")
        or request.accept_mimetypes.best == "application/json"
    )


def _get_task(task_id: int) -> Task:
    """Fetch a task that belongs to the current user (404 otherwise)."""
    task = db.session.get(Task, task_id)
    if task is None or task.user_id != current_user.id:
        abort(404)
    return task


def _selected_ids() -> list[int]:
    ids: list[int] = []
    for raw in request.form.getlist("ids") or request.form.getlist("task_ids"):
        with contextlib.suppress(TypeError, ValueError):
            ids.append(int(raw))
    single = request.form.get("id")
    if single:
        with contextlib.suppress(TypeError, ValueError):
            ids.append(int(single))
    return sorted(set(ids))


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


@bp.get("/")
@login_required
def index():
    form = FilterForm(request.args, meta={"csrf": False})
    query = Task.for_user(current_user)

    status = (request.args.get("status") or "").strip()
    kind = (request.args.get("kind") or "").strip()
    collection = (request.args.get("collection") or "").strip()
    term = (request.args.get("q") or "").strip()
    when = (request.args.get("when") or "all").strip()
    include_done = request.args.get("include_done", "1") not in {"0", "false", "no"}

    if status in TASK_STATUSES:
        query = query.filter(Task.status == status)
    elif not include_done:
        query = query.filter(Task.status == STATUS_OPEN)
    if kind in TASK_KINDS:
        query = query.filter(Task.kind == kind)
    if collection:
        query = query.filter(Task.collection == collection)
    if term:
        like = f"%{term}%"
        query = query.filter(db.or_(Task.title.ilike(like), Task.notes.ilike(like)))

    today = today_local()
    if when == "today":
        query = query.filter(Task.scheduled_date == today)
    elif when == "overdue":
        query = query.filter(Task.scheduled_date < today, Task.status == STATUS_OPEN)
    elif when == "future":
        query = query.filter(Task.scheduled_date > today)
    elif when == "week":
        query = query.filter(Task.scheduled_date >= today, Task.scheduled_date <= today + timedelta(days=7))

    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(Task.scheduled_date.desc(), Task.position, Task.id.desc()).paginate(
        page=max(page, 1), per_page=current_app_per_page(), error_out=False
    )

    form.collection.choices = [("", "Todas")] + [(c, c) for c in Task.collections(current_user)]

    return render_template(
        "tasks/index.html",
        pagination=pagination,
        tasks=pagination.items,
        form=form,
        bulk_actions=BULK_ACTIONS,
        stats=Task.stats(current_user),
        filters={
            "status": status,
            "kind": kind,
            "collection": collection,
            "q": term,
            "when": when,
            "include_done": include_done,
        },
    )


def current_app_per_page() -> int:
    from flask import current_app

    return int(current_app.config.get("TASKS_PER_PAGE", 50))


@bp.get("/<int:task_id>")
@login_required
def detail(task_id: int):
    task = _get_task(task_id)
    return render_template("tasks/detail.html", task=task)


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    form = TaskForm()
    if request.method == "GET":
        preset = parse_date(request.args.get("date"), today_local())
        form.scheduled_date.data = preset
        kind = request.args.get("kind")
        if kind in TASK_KINDS:
            form.kind.data = kind

    if form.validate_on_submit():
        task = Task(user_id=current_user.id)
        _apply_form(task, form)
        task.status = form.status.data or STATUS_OPEN
        if task.status == STATUS_DONE:
            task.complete()
        else:
            task.status = STATUS_OPEN
        db.session.add(task)
        db.session.commit()
        flash("Tarefa criada.", "success")
        return redirect(_next_url(task))

    return render_template("tasks/form.html", form=form, task=None)


@bp.post("/quick")
@login_required
def quick_add():
    form = QuickTaskForm()
    if form.validate_on_submit():
        task = Task(
            user_id=current_user.id,
            title=form.title.data.strip(),
            kind=form.kind.data if form.kind.data in TASK_KINDS else KIND_TASK,
            priority=form.priority.data if form.priority.data in TASK_PRIORITIES else PRIORITY_NONE,
            collection=(form.collection.data or "").strip()[:64] or None,
            scheduled_date=form.scheduled_date.data or today_local(),
            status=STATUS_OPEN,
        )
        db.session.add(task)
        db.session.commit()
        if _wants_json():
            return jsonify({"ok": True, "task": _task_payload(task)}), 201
        flash("Tarefa adicionada.", "success")
        return redirect(request.form.get("next") or url_for("main.dashboard"))

    if _wants_json():
        return jsonify({"ok": False, "errors": form.errors}), 400
    for errors in form.errors.values():
        for error in errors:
            flash(error, "danger")
    return redirect(request.form.get("next") or url_for("main.dashboard"))


@bp.route("/<int:task_id>/edit", methods=["GET", "POST"])
@login_required
def edit(task_id: int):
    task = _get_task(task_id)
    form = TaskForm(obj=task)
    if form.validate_on_submit():
        _apply_form(task, form)
        requested = form.status.data or task.status
        if requested == STATUS_DONE and not task.is_done:
            task.complete()
        elif requested == STATUS_OPEN and not task.is_open:
            task.reopen()
        else:
            task.status = requested
            if requested != STATUS_DONE:
                task.completed_at = None
        db.session.commit()
        flash("Tarefa atualizada.", "success")
        return redirect(_next_url(task))

    return render_template("tasks/form.html", form=form, task=task)


def _apply_form(task: Task, form: TaskForm) -> None:
    task.title = form.title.data.strip()
    task.notes = (form.notes.data or "").strip() or None
    task.kind = form.kind.data if form.kind.data in TASK_KINDS else KIND_TASK
    task.priority = form.priority.data if form.priority.data in TASK_PRIORITIES else PRIORITY_NONE
    task.collection = (form.collection.data or "").strip()[:64] or None
    task.scheduled_date = form.scheduled_date.data or today_local()
    task.due_date = form.due_date.data


def _next_url(task: Task) -> str:
    target = request.form.get("next") or request.args.get("next")
    if target:
        return target
    if task.scheduled_date and task.scheduled_date > today_local():
        return url_for("main.future_log")
    return url_for("main.dashboard")


@bp.post("/<int:task_id>/toggle")
@login_required
def toggle(task_id: int):
    task = _get_task(task_id)
    now_done = task.toggle()
    db.session.commit()
    if _wants_json():
        return jsonify({"ok": True, "task": _task_payload(task)})
    flash("Tarefa concluída! ✕" if now_done else "Tarefa reaberta.", "success" if now_done else "info")
    return redirect(request.form.get("next") or request.referrer or url_for("main.dashboard"))


@bp.post("/<int:task_id>/delete")
@login_required
def delete(task_id: int):
    task = _get_task(task_id)
    db.session.delete(task)
    db.session.commit()
    if _wants_json():
        return jsonify({"ok": True, "deleted": task_id})
    flash("Tarefa removida.", "info")
    return redirect(request.form.get("next") or url_for("tasks.index"))


@bp.post("/<int:task_id>/migrate")
@login_required
def migrate(task_id: int):
    """BUJO migration: carry the entry forward and mark the original with ``>``."""
    task = _get_task(task_id)
    target = parse_date(request.form.get("target_date"))
    if target is None:
        preset = (request.form.get("preset") or "tomorrow").strip()
        if preset == "week":
            target = today_local() + timedelta(days=7)
        elif preset == "month":
            target = today_local() + timedelta(days=30)
        elif preset == "today":
            target = today_local()
        else:
            target = today_local() + timedelta(days=1)

    if target == task.scheduled_date and task.is_open:
        flash("A tarefa já está agendada para essa data.", "warning")
        return redirect(request.form.get("next") or url_for("main.dashboard"))

    successor = task.migrate_to(target)
    db.session.commit()
    if _wants_json():
        return jsonify({"ok": True, "task": _task_payload(task), "successor": _task_payload(successor)})
    flash(f"Tarefa migrada para {target.strftime('%d/%m/%Y')} (>).", "info")
    return redirect(request.form.get("next") or url_for("main.dashboard"))


@bp.post("/bulk")
@login_required
def bulk():
    action = (request.form.get("action") or "").strip()
    ids = _selected_ids()
    if not ids:
        flash("Nenhuma tarefa selecionada.", "warning")
        return redirect(request.form.get("next") or url_for("tasks.index"))
    if action not in BULK_ACTIONS:
        flash("Ação inválida.", "danger")
        return redirect(request.form.get("next") or url_for("tasks.index"))

    tasks = Task.for_user(current_user).filter(Task.id.in_(ids)).all()
    count = len(tasks)

    if action == "complete":
        for task in tasks:
            task.complete()
    elif action == "reopen":
        for task in tasks:
            task.reopen()
    elif action == "cancel":
        for task in tasks:
            task.cancel()
    elif action in {"migrate_tomorrow", "migrate_week"}:
        target = today_local() + timedelta(days=1 if action == "migrate_tomorrow" else 7)
        for task in tasks:
            if task.is_open:
                task.migrate_to(target)
    elif action == "delete":
        for task in tasks:
            db.session.delete(task)

    db.session.commit()
    flash(f"{BULK_ACTIONS[action]}: {count} tarefa(s).", "success")
    return redirect(request.form.get("next") or url_for("tasks.index"))


@bp.post("/<int:task_id>/reschedule")
@login_required
def reschedule(task_id: int):
    """Move a task to another day — used by calendar drag & drop."""
    task = _get_task(task_id)
    payload = request.get_json(silent=True) or request.form
    target = parse_date(payload.get("date") or payload.get("scheduled_date"))
    if target is None:
        return jsonify({"ok": False, "error": "Data inválida."}), 400
    task.scheduled_date = target
    if task.due_date and task.due_date < target:
        task.due_date = target
    db.session.commit()
    return jsonify({"ok": True, "task": _task_payload(task)})


def _task_payload(task: Task) -> dict:
    data = task.to_dict()
    data.update(
        {
            "id": task.id,
            "url": url_for("tasks.detail", task_id=task.id),
            "edit_url": url_for("tasks.edit", task_id=task.id),
            "toggle_url": url_for("tasks.toggle", task_id=task.id),
            "delete_url": url_for("tasks.delete", task_id=task.id),
            "bujo_symbol": task.bujo_symbol,
        }
    )
    return data


# Convenience endpoint used by the "empty state" buttons.
@bp.post("/delete-completed")
@login_required
def delete_completed():
    tasks = Task.for_user(current_user).filter(Task.status.in_([STATUS_DONE, STATUS_CANCELLED])).all()
    count = len(tasks)
    for task in tasks:
        db.session.delete(task)
    db.session.commit()
    flash(f"{count} tarefa(s) concluída(s)/cancelada(s) removida(s).", "info")
    return redirect(request.form.get("next") or url_for("tasks.index"))
