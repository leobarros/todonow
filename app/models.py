"""Database models for todonow.

The vocabulary follows the BUJO (bullet journal) method: every entry is a
*task*, an *event* or a *note*, and its lifecycle is tracked through the
``status`` column (open, done, migrated, cancelled).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

from flask_login import UserMixin
from sqlalchemy import Index, UniqueConstraint, func
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db
from .utils import (
    KIND_SYMBOLS,
    KIND_TASK,
    PRIORITY_NONE,
    PRIORITY_SIGNIFIERS,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_LABELS,
    STATUS_MIGRATED,
    STATUS_OPEN,
    STATUS_SYMBOLS,
    get_timezone,
    iso,
    now_local,
    parse_date,
    parse_datetime,
    today_local,
)


def _uuid() -> str:
    return uuid.uuid4().hex


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=now_local, nullable=False)
    updated_at = db.Column(db.DateTime, default=now_local, onupdate=now_local, nullable=False)


class User(TimestampMixin, UserMixin, db.Model):
    """Local account. Authentication is username + password, stored hashed."""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=True)
    display_name = db.Column(db.String(120), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_active_flag = db.Column("is_active", db.Boolean, default=True, nullable=False)
    theme = db.Column(db.String(16), default="light", nullable=False)
    last_login_at = db.Column(db.DateTime, nullable=True)

    tasks = db.relationship(
        "Task",
        back_populates="owner",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="Task.scheduled_date",
    )

    # --- authentication -------------------------------------------------
    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        if not self.password_hash or not password:
            return False
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self) -> bool:  # Flask-Login hook
        return bool(self.is_active_flag)

    @is_active.setter
    def is_active(self, value: bool) -> None:
        self.is_active_flag = bool(value)

    @property
    def name(self) -> str:
        return self.display_name or self.username

    @property
    def initials(self) -> str:
        source = self.name.strip()
        parts = [p for p in source.replace("_", " ").replace(".", " ").split() if p]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()

    # --- serialization --------------------------------------------------
    def to_dict(self, include_password_hash: bool = False, include_tasks: bool = False) -> dict:
        data = {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "display_name": self.display_name,
            "is_admin": bool(self.is_admin),
            "is_active": bool(self.is_active_flag),
            "theme": self.theme,
            "created_at": iso(self.created_at),
            "last_login_at": iso(self.last_login_at),
        }
        if include_password_hash:
            data["password_hash"] = self.password_hash
        if include_tasks:
            data["tasks"] = [t.to_dict() for t in self.tasks]
        return data

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username}>"


class Task(TimestampMixin, db.Model):
    """A single bullet-journal entry belonging to exactly one user."""

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("uuid", name="uq_tasks_uuid"),
        Index("ix_tasks_user_scheduled", "user_id", "scheduled_date"),
        Index("ix_tasks_user_status", "user_id", "status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(32), default=_uuid, nullable=False, index=True)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    title = db.Column(db.String(255), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    kind = db.Column(db.String(16), default=KIND_TASK, nullable=False)
    status = db.Column(db.String(16), default=STATUS_OPEN, nullable=False, index=True)
    priority = db.Column(db.String(16), default=PRIORITY_NONE, nullable=False)
    # Campo legado: saiu da interface, mas continua no banco e no backup para
    # não perder dados de instâncias antigas.
    collection = db.Column(db.String(64), nullable=True, index=True)

    # The day the task "starts" — defaults to today, may be scheduled ahead.
    scheduled_date = db.Column(db.Date, default=today_local, nullable=False)
    # Optional hard deadline, which may differ from the start day.
    due_date = db.Column(db.Date, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    migrated_to_date = db.Column(db.Date, nullable=True)
    # Histórico: quantas vezes a entrada rolou para outro dia e quando.
    migration_count = db.Column(db.Integer, default=0, nullable=False, server_default="0")
    migrated_at = db.Column(db.DateTime, nullable=True)
    # Legado: a migração antiga criava uma cópia e apontava para a original.
    migrated_from_id = db.Column(db.Integer, db.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)

    position = db.Column(db.Integer, default=0, nullable=False)

    owner = db.relationship("User", back_populates="tasks")
    migrated_from = db.relationship("Task", remote_side=[id], uselist=False)

    # --- state helpers --------------------------------------------------
    @property
    def is_done(self) -> bool:
        return self.status == STATUS_DONE

    @property
    def is_open(self) -> bool:
        return self.status == STATUS_OPEN

    @property
    def is_migrated(self) -> bool:
        """Dados antigos marcados com a situação ``migrated``."""
        return self.status == STATUS_MIGRATED

    @property
    def was_migrated(self) -> bool:
        """A entrada já rolou de dia alguma vez (não é conclusão)."""
        return bool(self.migration_count)

    @property
    def is_cancelled(self) -> bool:
        return self.status == STATUS_CANCELLED

    @property
    def is_closed(self) -> bool:
        return self.status in {STATUS_DONE, STATUS_CANCELLED}

    @property
    def is_future(self) -> bool:
        return self.scheduled_date is not None and self.scheduled_date > today_local()

    def is_overdue(self, reference: date | None = None) -> bool:
        reference = reference or today_local()
        if not self.is_open or self.scheduled_date is None:
            return False
        if self.scheduled_date < reference:
            return True
        return bool(self.due_date and self.due_date < reference)

    @property
    def is_strikethrough(self) -> bool:
        """Completed or cancelled entries render with a line through them."""
        return self.status in {STATUS_DONE, STATUS_CANCELLED}

    @property
    def bujo_symbol(self) -> str:
        if self.status in STATUS_SYMBOLS and STATUS_SYMBOLS[self.status]:
            return STATUS_SYMBOLS[self.status]
        return KIND_SYMBOLS.get(self.kind, "•")

    @property
    def signifier(self) -> str:
        return PRIORITY_SIGNIFIERS.get(self.priority, "")

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def days_until_due(self) -> int | None:
        if not self.due_date:
            return None
        return (self.due_date - today_local()).days

    # --- transitions ----------------------------------------------------
    def complete(self) -> None:
        self.status = STATUS_DONE
        self.completed_at = now_local()

    def reopen(self) -> None:
        self.status = STATUS_OPEN
        self.completed_at = None

    def cancel(self) -> None:
        self.status = STATUS_CANCELLED
        self.completed_at = None

    def toggle(self) -> bool:
        """Flip between open and done; returns True when it became done."""
        if self.status == STATUS_DONE:
            self.reopen()
            return False
        self.complete()
        return True

    def migrate_to(self, target: date) -> Task:
        """BUJO: rola a entrada para *target* — movendo a mesma tarefa.

        Migrar **não** é concluir e **não** cria cópia: a tarefa apenas muda de
        dia (o histórico fica em ``migration_count``/``migrated_at``). Cópias
        geravam duplicidade no mesmo dia, que não existe na vida real.
        """
        target = parse_date(target, today_local() + timedelta(days=1))
        self.scheduled_date = target
        self.migrated_to_date = target
        self.migration_count = (self.migration_count or 0) + 1
        self.migrated_at = now_local()
        self.completed_at = None
        # Uma entrada migrada volta a ser uma pendência normal.
        if self.status == STATUS_MIGRATED:
            self.status = STATUS_OPEN
        return self

    # --- queries --------------------------------------------------------
    @classmethod
    def for_user(cls, user):
        return cls.query.filter_by(user_id=user.id)

    @classmethod
    def scheduled_on(cls, user, day: date):
        return cls.for_user(user).filter(cls.scheduled_date == day).order_by(cls.position, cls.id)

    @classmethod
    def pending_before(cls, user, day: date):
        """Open entries from previous days that are still rolling."""
        return (
            cls.for_user(user)
            .filter(cls.scheduled_date < day, cls.status == STATUS_OPEN)
            .order_by(cls.scheduled_date, cls.position, cls.id)
        )

    @classmethod
    def upcoming(cls, user, after: date, until: date | None = None, limit: int | None = None):
        query = cls.for_user(user).filter(cls.scheduled_date > after, cls.status == STATUS_OPEN)
        if until is not None:
            query = query.filter(cls.scheduled_date <= until)
        query = query.order_by(cls.scheduled_date, cls.position, cls.id)
        return query.limit(limit) if limit else query

    @classmethod
    def between(cls, user, start: date, end: date, include_closed: bool = True):
        query = cls.for_user(user).filter(cls.scheduled_date >= start, cls.scheduled_date <= end)
        if not include_closed:
            query = query.filter(cls.status == STATUS_OPEN)
        return query.order_by(cls.scheduled_date, cls.position, cls.id)

    @classmethod
    def completed_on(cls, user, day: date):
        return (
            cls.for_user(user)
            .filter(cls.status == STATUS_DONE, func.date(cls.completed_at) == day.isoformat())
            .order_by(cls.completed_at)
        )

    @classmethod
    def stats(cls, user) -> dict:
        day = today_local()
        base = cls.for_user(user)
        open_count = base.filter(cls.status == STATUS_OPEN).count()
        done_count = base.filter(cls.status == STATUS_DONE).count()
        overdue = base.filter(cls.status == STATUS_OPEN, cls.scheduled_date < day).count()
        today_count = base.filter(cls.scheduled_date == day).count()
        future = base.filter(cls.status == STATUS_OPEN, cls.scheduled_date > day).count()
        total = base.count()
        return {
            "total": total,
            "open": open_count,
            "done": done_count,
            "overdue": overdue,
            "today": today_count,
            "future": future,
            "completion_rate": round(done_count / total * 100) if total else 0,
        }

    # --- serialization --------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "uuid": self.uuid,
            "title": self.title,
            "notes": self.notes,
            "kind": self.kind,
            "status": self.status,
            "priority": self.priority,
            "collection": self.collection,
            "scheduled_date": iso(self.scheduled_date),
            "due_date": iso(self.due_date),
            "completed_at": iso(self.completed_at),
            "migrated_to_date": iso(self.migrated_to_date),
            "migration_count": self.migration_count or 0,
            "migrated_at": iso(self.migrated_at),
            "position": self.position,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }

    @classmethod
    def from_dict(cls, data: dict, user_id: int) -> Task:
        """Rebuild a task from a backup payload (never trusts ids/uuids blindly)."""
        task = cls(user_id=user_id)
        task.apply_dict(data)
        return task

    def apply_dict(self, data: dict) -> None:
        from .utils import KIND_TASK, PRIORITY_NONE, TASK_KINDS, TASK_PRIORITIES, TASK_STATUSES

        self.uuid = str(data.get("uuid") or self.uuid or _uuid())[:32]
        title = (data.get("title") or "").strip()
        self.title = title[:255] or "Sem título"
        notes = data.get("notes")
        self.notes = notes if notes in (None, "") else str(notes)
        self.kind = data.get("kind") if data.get("kind") in TASK_KINDS else KIND_TASK
        self.status = data.get("status") if data.get("status") in TASK_STATUSES else STATUS_OPEN
        self.priority = data.get("priority") if data.get("priority") in TASK_PRIORITIES else PRIORITY_NONE
        collection = data.get("collection")
        self.collection = (str(collection).strip()[:64] or None) if collection else None
        self.scheduled_date = parse_date(data.get("scheduled_date"), today_local())
        self.due_date = parse_date(data.get("due_date"))
        self.completed_at = parse_datetime(data.get("completed_at"))
        self.migrated_to_date = parse_date(data.get("migrated_to_date"))
        try:
            self.migration_count = int(data.get("migration_count") or 0)
        except (TypeError, ValueError):
            self.migration_count = 0
        self.migrated_at = parse_datetime(data.get("migrated_at"))
        try:
            self.position = int(data.get("position") or 0)
        except (TypeError, ValueError):
            self.position = 0
        created = parse_datetime(data.get("created_at"))
        if created:
            self.created_at = created
        updated = parse_datetime(data.get("updated_at"))
        if updated:
            self.updated_at = updated
        if self.status == STATUS_DONE and self.completed_at is None:
            self.completed_at = now_local()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Task {self.uuid[:8]} {self.title[:30]!r} {self.status}>"


class ClientOperation(TimestampMixin, db.Model):
    """Ação já aplicada, identificada por uma chave vinda do cliente.

    Existe por causa de uma instabilidade clássica de rede: o servidor processa
    o pedido, mas a resposta se perde antes de chegar ao navegador. Sem um
    registro, o reenvio criaria a tarefa duas vezes. Com ele, o reenvio devolve
    exatamente a mesma resposta da primeira vez.

    É uma tabela de passagem: as linhas são podadas depois de alguns dias (veja
    :func:`app.offline.prune`) e não entram no backup, que guarda só o que o
    usuário criou.
    """

    __tablename__ = "client_operations"
    __table_args__ = (
        # A rota faz parte da identidade: a mesma chave em rotas diferentes são
        # ações diferentes (e o reenvio de uma não pode anular a outra).
        UniqueConstraint("user_id", "key", "endpoint", name="uq_client_operations_user_key"),
        Index("ix_client_operations_created", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Chave de idempotência gerada no navegador (UUID), única por usuário.
    key = db.Column(db.String(80), nullable=False)
    # Endpoint que aplicou a ação: a mesma chave em outra rota não é replay.
    endpoint = db.Column(db.String(64), nullable=False)
    status_code = db.Column(db.Integer, default=200, nullable=False)
    response_json = db.Column(db.Text, nullable=True)

    def payload(self) -> dict:
        """Resposta gravada, pronta para ser devolvida de novo."""
        if not self.response_json:
            return {}
        try:
            data = json.loads(self.response_json)
        except ValueError:  # pragma: no cover - só com banco adulterado
            return {}
        return data if isinstance(data, dict) else {}

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ClientOperation {self.endpoint} {self.key[:8]}>"


def local_tz():
    """Expose the configured timezone for templates/CLI."""
    return get_timezone()
