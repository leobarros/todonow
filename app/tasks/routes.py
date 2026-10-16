"""Task CRUD, completion, migration and reschedule routes."""

from __future__ import annotations

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

from .. import markdown_notes, offline
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
from .forms import QuickTaskForm, TaskForm


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


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


@bp.get("/")
@login_required
def index():
    query = Task.for_user(current_user)

    status = (request.args.get("status") or "").strip()
    kind = (request.args.get("kind") or "").strip()
    term = (request.args.get("q") or "").strip()
    when = (request.args.get("when") or "all").strip()
    # O formulário manda um oculto "0" e, quando ligado, o checkbox "1" — vale o
    # último. Sem parâmetro nenhum (link direto, menu), mostra tudo.
    marcados = request.args.getlist("include_done")
    include_done = marcados[-1] not in {"0", "false", "no"} if marcados else True

    if status in TASK_STATUSES:
        query = query.filter(Task.status == status)
    elif not include_done:
        query = query.filter(Task.status == STATUS_OPEN)
    if kind in TASK_KINDS:
        query = query.filter(Task.kind == kind)
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

    return render_template(
        "tasks/index.html",
        pagination=pagination,
        tasks=pagination.items,
        stats=Task.stats(current_user),
        filters={
            "status": status,
            "kind": kind,
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

    # Um reenvio do modo offline (mesma chave) devolve a resposta da primeira vez.
    key = offline.idempotency_key()
    stored = offline.replay(key, "tasks.create")
    if stored is not None:
        return stored

    if form.validate_on_submit():
        task = Task(user_id=current_user.id)
        _apply_form(task, form)
        task.status = form.status.data or STATUS_OPEN
        if task.status == STATUS_DONE:
            task.complete()
        else:
            task.status = STATUS_OPEN
        db.session.add(task)
        db.session.flush()  # garante task.id para as URLs do payload
        redirect_to = _next_url(task)
        payload = {"ok": True, "task": _task_payload(task), "redirect": redirect_to}
        offline.remember(key, "tasks.create", payload, 201)
        offline.commit(key, "tasks.create")
        if _wants_json():
            return jsonify(payload), 201
        flash("Tarefa criada.", "success")
        return redirect(redirect_to)

    if _wants_json():
        return jsonify({"ok": False, "errors": form.errors}), 400
    return render_template("tasks/form.html", form=form, task=None)


@bp.post("/quick")
@login_required
def quick_add():
    form = QuickTaskForm()
    key = offline.idempotency_key()
    stored = offline.replay(key, "tasks.quick_add")
    if stored is not None:
        return stored

    if form.validate_on_submit():
        task = Task(
            user_id=current_user.id,
            title=form.title.data.strip(),
            kind=form.kind.data if form.kind.data in TASK_KINDS else KIND_TASK,
            priority=form.priority.data if form.priority.data in TASK_PRIORITIES else PRIORITY_NONE,
            scheduled_date=form.scheduled_date.data or today_local(),
            status=STATUS_OPEN,
        )
        db.session.add(task)
        db.session.flush()
        payload = {"ok": True, "task": _task_payload(task)}
        offline.remember(key, "tasks.quick_add", payload, 201)
        offline.commit(key, "tasks.quick_add")
        if _wants_json():
            return jsonify(payload), 201
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
    key = offline.idempotency_key()
    stored = offline.replay(key, "tasks.edit")
    if stored is not None:
        return stored

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
        db.session.flush()
        redirect_to = _next_url(task)
        payload = {"ok": True, "task": _task_payload(task), "redirect": redirect_to}
        offline.remember(key, "tasks.edit", payload)
        offline.commit(key, "tasks.edit")
        if _wants_json():
            return jsonify(payload)
        flash("Tarefa atualizada.", "success")
        return redirect(redirect_to)

    if _wants_json():
        return jsonify({"ok": False, "errors": form.errors}), 400
    return render_template("tasks/form.html", form=form, task=task)


def _apply_form(task: Task, form: TaskForm) -> None:
    task.title = form.title.data.strip()
    task.notes = (form.notes.data or "").strip() or None
    task.kind = form.kind.data if form.kind.data in TASK_KINDS else KIND_TASK
    task.priority = form.priority.data if form.priority.data in TASK_PRIORITIES else PRIORITY_NONE
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


@bp.post("/<int:task_id>/status")
@login_required
def set_status(task_id: int):
    """Define a situação da tarefa de forma **absoluta**.

    O ``toggle`` inverte o que estiver no banco, então repetir o pedido desfaz a
    ação — um clique duplo, ou um reenvio do modo offline, voltaria a tarefa
    para "aberta". Aqui o cliente diz qual situação quer ("concluída"), o que
    torna a operação idempotente: aplicar duas vezes dá no mesmo.
    """
    key = offline.idempotency_key()
    stored = offline.replay(key, "tasks.set_status")
    if stored is not None:
        return stored

    task = _get_task(task_id)

    payload_dados = request.get_json(silent=True) or {}
    status = (request.form.get("status") or payload_dados.get("status") or "").strip()
    destino = request.form.get("next") or request.referrer or url_for("main.dashboard")

    if status not in {STATUS_OPEN, STATUS_DONE}:
        if _wants_json():
            return jsonify({"ok": False, "error": "Situação inválida."}), 400
        flash("Situação inválida.", "danger")
        return redirect(destino)

    mudou = False
    if status == STATUS_DONE and not task.is_done:
        task.complete()
        mudou = True
    elif status == STATUS_OPEN and not task.is_open:
        task.reopen()
        mudou = True

    db.session.flush()
    payload = {"ok": True, "changed": mudou, "task": _task_payload(task)}
    offline.remember(key, "tasks.set_status", payload)
    offline.commit(key, "tasks.set_status")

    if _wants_json():
        return jsonify(payload)
    flash("Tarefa concluída! ✕" if status == STATUS_DONE else "Tarefa reaberta.", "success" if mudou else "info")
    return redirect(destino)


@bp.post("/<int:task_id>/delete")
@login_required
def delete(task_id: int):
    # O replay vem antes de buscar a tarefa: na segunda vez ela já não existe,
    # e um 404 faria o cliente tratar como falha o que na verdade deu certo.
    key = offline.idempotency_key()
    stored = offline.replay(key, "tasks.delete")
    if stored is not None:
        return stored

    task = _get_task(task_id)
    db.session.delete(task)
    payload = {"ok": True, "deleted": task_id}
    offline.remember(key, "tasks.delete", payload)
    offline.commit(key, "tasks.delete")
    if _wants_json():
        return jsonify(payload)
    flash("Tarefa removida.", "info")
    return redirect(request.form.get("next") or url_for("tasks.index"))


@bp.post("/<int:task_id>/migrate")
@login_required
def migrate(task_id: int):
    """Adia a entrada (migração BUJO): a mesma tarefa muda de dia.

    Os atalhos são **relativos à data atual da tarefa** (nunca "hoje + 1"), para
    que adiar uma tarefa já agendada para amanhã não tente mirar o próprio dia.
    """
    key = offline.idempotency_key()
    stored = offline.replay(key, "tasks.migrate")
    if stored is not None:
        return stored

    task = _get_task(task_id)
    base = task.scheduled_date or today_local()
    target = parse_date(request.form.get("target_date"))

    if target is None:
        preset = (request.form.get("preset") or "next_day").strip()
        deslocamento = {"next_day": 1, "next_week": 7, "next_month": 30}.get(preset, 1)
        target = base + timedelta(days=deslocamento)

    destino = request.form.get("next") or url_for("main.dashboard")

    if target <= base:
        flash(
            f"Para adiar, escolha uma data depois de {base.strftime('%d/%m/%Y')}.",
            "warning",
        )
        return redirect(destino)

    # Não cria uma gêmea: se já existe uma tarefa aberta com este nome no dia
    # de destino, isso viraria duplicidade no mesmo dia.
    irma = (
        Task.for_user(current_user)
        .filter(
            Task.id != task.id,
            Task.status == STATUS_OPEN,
            Task.scheduled_date == target,
            db.func.lower(db.func.trim(Task.title)) == task.title.strip().lower(),
        )
        .first()
    )
    if irma is not None:
        flash(
            f'Já existe uma tarefa aberta com este nome em {target.strftime("%d/%m/%Y")} '
            f"(#{irma.id}). Nada foi alterado — edite, adie ou remova uma das duas.",
            "warning",
        )
        return redirect(destino)

    task.migrate_to(target)
    db.session.flush()
    payload = {"ok": True, "task": _task_payload(task)}
    offline.remember(key, "tasks.migrate", payload)
    offline.commit(key, "tasks.migrate")
    if _wants_json():
        return jsonify(payload)
    flash(f'Tarefa adiada para {target.strftime("%d/%m/%Y")}.', "info")
    return redirect(destino)


@bp.post("/<int:task_id>/notes")
@login_required
def save_notes(task_id: int):
    """Grava as anotações da tarefa (usado pelo editor da tela de detalhe)."""
    key = offline.idempotency_key()
    stored = offline.replay(key, "tasks.save_notes")
    if stored is not None:
        return stored

    task = _get_task(task_id)

    # O botão "Limpar" manda clear_notes=1; o textarea continua sendo a fonte do texto.
    if request.form.get("clear_notes"):
        texto = ""
    else:
        texto = (request.form.get("notes") or "").strip()
    limite = 20000
    if len(texto) > limite:
        flash(f"As anotações passam de {limite} caracteres. Nada foi salvo.", "danger")
    else:
        task.notes = texto or None
        db.session.flush()
        offline.remember(key, "tasks.save_notes", {"ok": True})
        offline.commit(key, "tasks.save_notes")
        flash("Anotações salvas." if texto else "Anotações removidas.", "success")

    if _wants_json():
        return jsonify({"ok": True, "notes": task.notes, "html": str(markdown_notes.render(task.notes))})
    return redirect(request.form.get("next") or url_for("tasks.detail", task_id=task.id))


@bp.post("/notes/preview")
@login_required
def render_notes():
    """Markdown → HTML sanitizado.

    Serve ao editor (carregar o modo Visual a partir do Markdown salvo) e a
    qualquer pré-visualização: é o mesmo renderizador que exibe a nota depois.
    """
    payload = request.get_json(silent=True) or request.form
    texto = (payload.get("notes") or "")[:20000]
    return jsonify({"ok": True, "html": str(markdown_notes.render(texto))})


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
