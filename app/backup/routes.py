"""Backup routes: JSON export, import/restore and server-side snapshots."""

from __future__ import annotations

import json
from datetime import datetime

from flask import (
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required

from ..models import Task
from ..utils import slugify
from . import bp, service
from .forms import ExportForm, ImportForm, RestoreSnapshotForm, SnapshotForm


def _require_admin_for(scope: str) -> None:
    if scope == "full" and not current_user.is_admin:
        abort(403)


@bp.get("/")
@login_required
def index():
    export_form = ExportForm()
    import_form = ImportForm()
    snapshot_form = SnapshotForm()
    restore_form = RestoreSnapshotForm()
    return render_template(
        "backup/index.html",
        export_form=export_form,
        import_form=import_form,
        snapshot_form=snapshot_form,
        restore_form=restore_form,
        snapshots=service.list_snapshots(),
        stats=Task.stats(current_user),
        total_tasks=Task.for_user(current_user).count(),
        backup_dir=str(current_app.config["BACKUP_DIR"]),
        keep=current_app.config.get("BACKUP_KEEP", 20),
    )


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


@bp.route("/export", methods=["GET", "POST"])
@login_required
def export():
    form = ExportForm()
    if request.method == "POST" and not form.validate_on_submit():
        flash("Escopo inválido.", "danger")
        return redirect(url_for("backup.index"))

    scope = (request.values.get("scope") or "user").strip().lower()
    _require_admin_for(scope)

    payload = service.export_full() if scope == "full" else service.export_tasks(current_user)
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    filename = f"todonow-{scope}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"

    if request.values.get("save") == "1":
        path = service.save_snapshot(payload, scope, None if scope == "full" else current_user)
        flash(f"Backup salvo no servidor: {path.name}", "success")
        return redirect(url_for("backup.index"))

    return Response(
        body,
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.post("/snapshot")
@login_required
def create_snapshot():
    form = SnapshotForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            for error in errors:
                flash(error, "danger")
        return redirect(url_for("backup.index"))

    scope = form.scope.data
    _require_admin_for(scope)
    payload = service.export_full() if scope == "full" else service.export_tasks(current_user)
    path = service.save_snapshot(payload, scope, None if scope == "full" else current_user)

    label = (form.label.data or "").strip()
    if label:
        target = path.with_name(f"{slugify(label)}-{path.name}")
        try:
            path.rename(target)
            path = target
        except OSError:  # pragma: no cover - keep the generated name on failure
            pass

    flash(f"Snapshot criado: {path.name}", "success")
    return redirect(url_for("backup.index"))


def _authorize_snapshot(name: str) -> dict:
    """Load a snapshot and ensure the current user may touch it.

    Full-instance snapshots are admin-only; user snapshots are restricted to
    their owner (admins may inspect any of them).
    """
    try:
        payload = service.load_snapshot(name)
    except service.RestoreError as exc:
        flash(str(exc), "danger")
        return {}
    scope = (payload.get("scope") or "user").strip().lower()
    if scope == "full":
        if not current_user.is_admin:
            abort(403)
    elif not current_user.is_admin:
        owner = (payload.get("user") or {}).get("username")
        if owner and owner != current_user.username:
            abort(403)
    return payload


@bp.get("/snapshot/<path:name>/download")
@login_required
def download_snapshot(name: str):
    if not _authorize_snapshot(name):
        return redirect(url_for("backup.index"))
    try:
        path = service.snapshot_path(name)
    except service.RestoreError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("backup.index"))
    return send_file(path, as_attachment=True, download_name=path.name, mimetype="application/json")


@bp.post("/snapshot/<path:name>/delete")
@login_required
def delete_snapshot(name: str):
    if not _authorize_snapshot(name):
        return redirect(url_for("backup.index"))
    try:
        removed = service.delete_snapshot(name)
    except service.RestoreError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("backup.index"))
    flash("Snapshot removido." if removed else "Não foi possível remover o snapshot.", "info" if removed else "warning")
    return redirect(url_for("backup.index"))


@bp.post("/snapshot/<path:name>/restore")
@login_required
def restore_snapshot(name: str):
    form = RestoreSnapshotForm()
    if not form.validate_on_submit():
        flash("Requisição inválida.", "danger")
        return redirect(url_for("backup.index"))
    payload = _authorize_snapshot(name)
    if not payload:
        return redirect(url_for("backup.index"))

    try:
        result = service.restore(payload, current_user, mode=form.mode.data)
    except service.RestoreError as exc:
        flash(f"Falha na restauração: {exc}", "danger")
        return redirect(url_for("backup.index"))
    except Exception as exc:  # pragma: no cover - defensive
        current_app.logger.exception("Restore from snapshot failed")
        flash(f"Erro inesperado ao restaurar: {exc}", "danger")
        return redirect(url_for("backup.index"))

    flash(f"Backup {name} restaurado. {_describe(result)}", "success")
    return redirect(url_for("backup.index"))


# --------------------------------------------------------------------------
# Import / restore from upload
# --------------------------------------------------------------------------


@bp.post("/import")
@login_required
def import_backup():
    form = ImportForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            for error in errors:
                flash(error, "danger")
        return redirect(url_for("backup.index"))

    try:
        if form.file.data and getattr(form.file.data, "filename", ""):
            raw = form.file.data.read()
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
        else:
            text = form.text.data or ""
        payload = service.payload_from_text(text)
        result = service.restore(payload, current_user, mode=form.mode.data)
    except service.RestoreError as exc:
        flash(f"Falha na restauração: {exc}", "danger")
        return redirect(url_for("backup.index"))
    except Exception as exc:  # pragma: no cover - defensive
        current_app.logger.exception("Import failed")
        flash(f"Erro inesperado ao importar: {exc}", "danger")
        return redirect(url_for("backup.index"))

    flash(f"Backup restaurado com sucesso. {_describe(result)}", "success")
    return redirect(url_for("backup.index"))


def _describe(result: dict) -> str:
    if result.get("scope") == "full":
        return (
            f"Usuários: +{result.get('users_created', 0)}/~{result.get('users_updated', 0)} · "
            f"Tarefas: +{result.get('tasks_created', 0)}/~{result.get('tasks_updated', 0)}"
            + (f" · removidas {result['tasks_deleted']}" if result.get("tasks_deleted") else "")
        )
    return f"Tarefas: +{result.get('created', 0)} criadas, {result.get('updated', 0)} atualizadas" + (
        f", {result['deleted']} removidas" if result.get("deleted") else ""
    )
