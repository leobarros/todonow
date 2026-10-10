"""Backup payload creation and restore logic.

Payload format (``todonow.backup``):

* user scope  -> top-level ``tasks`` list
* full  scope -> ``users`` list, each with a nested ``tasks`` list
                 (password hashes included so a restore keeps logins working)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from flask import current_app
from sqlalchemy import func

from .. import __version__
from ..extensions import db
from ..models import Task, User
from ..utils import today_local

FORMAT = "todonow.backup"
FORMAT_VERSION = 1
SNAPSHOT_SUFFIX = ".json"


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def _envelope(scope: str, extra: dict) -> dict:
    payload = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "app": "todonow",
        "app_version": __version__,
        "scope": scope,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {},
    }
    payload.update(extra)
    return payload


def export_tasks(user: User) -> dict:
    """Export every task that belongs to *user*."""
    tasks = Task.for_user(user).order_by(Task.scheduled_date, Task.id).all()
    payload = _envelope(
        "user",
        {
            "user": user.to_dict(),
            "tasks": [task.to_dict() for task in tasks],
        },
    )
    payload["counts"] = {"tasks": len(tasks), "users": 1}
    return payload


def export_full() -> dict:
    """Export the whole instance: users (with hashes) and their tasks."""
    users = User.query.order_by(User.id).all()
    payload = _envelope(
        "full",
        {
            "users": [user.to_dict(include_password_hash=True, include_tasks=True) for user in users],
        },
    )
    payload["counts"] = {
        "users": len(users),
        "tasks": sum(len(user.tasks) for user in users),
    }
    return payload


# --------------------------------------------------------------------------
# Restore
# --------------------------------------------------------------------------


class RestoreError(ValueError):
    """Raised when a payload cannot be restored."""


def validate_payload(payload) -> dict:
    if not isinstance(payload, dict):
        raise RestoreError("O arquivo não contém um objeto JSON válido.")
    if payload.get("format") not in (FORMAT, None) and "tasks" not in payload and "users" not in payload:
        raise RestoreError("Formato não reconhecido. Esperado um backup do todonow.")
    if "tasks" not in payload and "users" not in payload:
        raise RestoreError("Backup sem tarefas nem usuários.")
    return payload


def restore_tasks(user: User, payload: dict, mode: str = "merge") -> dict:
    """Restore a user-scope payload into *user*'s account."""
    validate_payload(payload)
    tasks = payload.get("tasks") or []
    if not isinstance(tasks, list):
        raise RestoreError("A lista de tarefas é inválida.")

    stats = {"created": 0, "updated": 0, "skipped": 0, "deleted": 0, "mode": mode}

    try:
        if mode == "replace":
            # ORM-level delete keeps the identity map consistent for the
            # re-insert below (SQLite reuses primary keys).
            doomed = Task.for_user(user).all()
            for task in doomed:
                db.session.delete(task)
            stats["deleted"] = len(doomed)
            db.session.flush()

        for item in tasks:
            if not isinstance(item, dict):
                stats["skipped"] += 1
                continue
            uuid = str(item.get("uuid") or "").strip()
            existing = None
            if uuid and mode != "replace":
                existing = Task.for_user(user).filter(Task.uuid == uuid).first()
            if existing is not None:
                existing.apply_dict(item)
                stats["updated"] += 1
            else:
                db.session.add(Task.from_dict(item, user.id))
                stats["created"] += 1
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return stats


def restore_full(payload: dict, mode: str = "merge", keep_admin_id: int | None = None) -> dict:
    """Restore a full-instance payload (users + tasks). Admin only."""
    validate_payload(payload)
    users = payload.get("users")
    if not isinstance(users, list):
        raise RestoreError("Backup completo sem lista de usuários.")

    stats = {"users_created": 0, "users_updated": 0, "tasks_created": 0, "tasks_updated": 0, "tasks_deleted": 0}

    try:
        for entry in users:
            if not isinstance(entry, dict):
                continue
            username = (entry.get("username") or "").strip()
            if not username:
                continue
            user = User.query.filter(func.lower(User.username) == username.lower()).first()
            if user is None:
                user = User(username=username, password_hash=entry.get("password_hash") or "!")
                db.session.add(user)
                db.session.flush()
                stats["users_created"] += 1
            else:
                stats["users_updated"] += 1

            user.email = entry.get("email") or None
            user.display_name = entry.get("display_name") or None
            user.is_admin = bool(entry.get("is_admin", user.is_admin))
            if entry.get("password_hash"):
                user.password_hash = entry["password_hash"]
            if "is_active" in entry:
                user.is_active_flag = bool(entry["is_active"])
            if user.theme in (None, ""):
                user.theme = entry.get("theme") or "light"

            if mode == "replace":
                doomed = Task.for_user(user).all()
                for task in doomed:
                    db.session.delete(task)
                stats["tasks_deleted"] += len(doomed)
                db.session.flush()

            for item in entry.get("tasks") or []:
                if not isinstance(item, dict):
                    continue
                uuid = str(item.get("uuid") or "").strip()
                existing = Task.for_user(user).filter(Task.uuid == uuid).first() if uuid else None
                if existing is not None:
                    existing.apply_dict(item)
                    stats["tasks_updated"] += 1
                else:
                    db.session.add(Task.from_dict(item, user.id))
                    stats["tasks_created"] += 1

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return stats


def restore(payload: dict, actor: User, mode: str = "merge") -> dict:
    """Dispatch to the right restore strategy based on the payload scope."""
    scope = (payload.get("scope") or "").strip().lower()
    looks_full = isinstance(payload.get("users"), list)
    if looks_full and scope != "user":
        if not actor.is_admin:
            raise RestoreError("Somente administradores podem restaurar backups completos.")
        if not current_app.config.get("ALLOW_FULL_RESTORE", True):
            raise RestoreError("A restauração completa está desativada neste servidor.")
        return {"scope": "full", **restore_full(payload, mode=mode)}
    return {"scope": "user", **restore_tasks(actor, payload, mode=mode)}


# --------------------------------------------------------------------------
# Server-side snapshots
# --------------------------------------------------------------------------


def backup_dir() -> Path:
    path = Path(current_app.config["BACKUP_DIR"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def snapshot_name(scope: str, user: User | None = None) -> str:
    # Microseconds keep rapid successive snapshots unique (same-second saves).
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    who = f"-{user.username}" if user is not None else ""
    return f"todonow-{scope}{who}-{stamp}{SNAPSHOT_SUFFIX}"


def save_snapshot(payload: dict, scope: str, user: User | None = None) -> Path:
    path = backup_dir() / snapshot_name(scope, user)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    prune_snapshots(scope=scope, user=user)
    return path


def list_snapshots(scope: str = "all") -> list[dict]:
    entries = []
    for path in sorted(backup_dir().glob(f"*{SNAPSHOT_SUFFIX}"), reverse=True):
        if scope != "all" and f"-{scope}" not in path.name:
            continue
        stat = path.stat()
        entries.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "size_human": _human_size(stat.st_size),
                "modified": datetime.fromtimestamp(stat.st_mtime),
                "path": path,
            }
        )
    return entries


def prune_snapshots(scope: str, user: User | None = None, keep: int | None = None) -> int:
    keep = keep if keep is not None else int(current_app.config.get("BACKUP_KEEP", 20))
    if keep <= 0:
        return 0
    pattern = (
        f"todonow-{scope}-*{SNAPSHOT_SUFFIX}" if user is None else f"todonow-{scope}-{user.username}-*{SNAPSHOT_SUFFIX}"
    )
    files = sorted(backup_dir().glob(pattern), reverse=True)
    removed = 0
    for path in files[keep:]:
        try:
            path.unlink()
            removed += 1
        except OSError:  # pragma: no cover - race with another process
            continue
    return removed


def load_snapshot(name: str) -> dict:
    path = _safe_snapshot_path(name)
    return json.loads(path.read_text(encoding="utf-8"))


def delete_snapshot(name: str) -> bool:
    path = _safe_snapshot_path(name)
    try:
        path.unlink()
        return True
    except OSError:
        return False


def snapshot_path(name: str) -> Path:
    return _safe_snapshot_path(name)


def _safe_snapshot_path(name: str) -> Path:
    """Resolve *name* inside the backup dir, refusing traversal attempts."""
    candidate = (backup_dir() / Path(name).name).resolve()
    root = backup_dir().resolve()
    if root not in candidate.parents and candidate != root:
        raise RestoreError("Caminho de backup inválido.")
    if candidate.suffix != SNAPSHOT_SUFFIX or not candidate.exists():
        raise RestoreError("Backup não encontrado.")
    return candidate


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def payload_from_text(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RestoreError(f"JSON inválido: {exc.msg} (linha {exc.lineno}).") from exc


def summary(payload: dict) -> dict:
    """Small summary used by the UI to describe a payload before restoring."""
    counts = payload.get("counts") or {}
    tasks = payload.get("tasks")
    if tasks is None:
        tasks = [t for user in (payload.get("users") or []) for t in (user.get("tasks") or [])]
    return {
        "scope": payload.get("scope") or ("full" if payload.get("users") else "user"),
        "exported_at": payload.get("exported_at"),
        "app_version": payload.get("app_version"),
        "users": counts.get("users", len(payload.get("users") or [])),
        "tasks": counts.get("tasks", len(tasks or [])),
        "today": today_local().isoformat(),
    }
