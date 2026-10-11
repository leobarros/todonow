"""Flask CLI commands — user management, backups and maintenance."""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import click
from flask.cli import with_appcontext

from .extensions import db
from .models import Task, User
from .utils import STATUS_CANCELLED, STATUS_DONE, STATUS_OPEN, now_local, today_local


def register_cli(app) -> None:
    app.cli.add_command(init_db)
    app.cli.add_command(create_user)
    app.cli.add_command(list_users)
    app.cli.add_command(set_password)
    app.cli.add_command(backup_cmd)
    app.cli.add_command(restore_cmd)
    app.cli.add_command(snapshots_cmd)
    app.cli.add_command(purge_completed)
    app.cli.add_command(seed_demo)
    app.cli.add_command(dedupe_tasks)


@click.command("init-db")
@with_appcontext
def init_db():
    """Create the database schema (idempotent)."""
    db.create_all()
    click.secho("Banco de dados pronto.", fg="green")


@click.command("create-user")
@click.argument("username")
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True, help="Senha do usuário")
@click.option("--email", default=None, help="E-mail opcional")
@click.option("--display-name", default=None, help="Nome de exibição")
@click.option("--admin", is_flag=True, help="Concede acesso de administrador")
@click.option("--only-if-missing", is_flag=True, help="Não altera nada se o usuário já existir")
@with_appcontext
def create_user(
    username: str,
    password: str,
    email: str | None,
    display_name: str | None,
    admin: bool,
    only_if_missing: bool,
):
    """Create a local user (or update the password of an existing one)."""
    existing = User.query.filter(db.func.lower(User.username) == username.lower()).first()
    if existing:
        if only_if_missing:
            click.secho(f"Usuário '{existing.username}' já existe — mantido como está.", fg="blue")
            return
        existing.set_password(password)
        if admin:
            existing.is_admin = True
        db.session.commit()
        click.secho(f"Senha de '{existing.username}' atualizada.", fg="yellow")
        return

    user = User(username=username.strip(), email=(email or None), display_name=(display_name or None))
    user.set_password(password)
    user.is_admin = bool(admin or User.query.count() == 0)
    db.session.add(user)
    db.session.commit()
    click.secho(f"Usuário '{user.username}' criado (admin={user.is_admin}).", fg="green")


@click.command("list-users")
@with_appcontext
def list_users():
    """List every account with task counters."""
    users = User.query.order_by(User.username).all()
    if not users:
        click.echo("Nenhum usuário cadastrado.")
        return
    click.echo(f"{'ID':>4}  {'USUÁRIO':<20} {'ADMIN':<6} {'ATIVO':<6} {'TAREFAS':>8}  ÚLTIMO LOGIN")
    for user in users:
        click.echo(
            f"{user.id:>4}  {user.username:<20} {str(user.is_admin):<6} {str(user.is_active):<6} "
            f"{len(user.tasks):>8}  {user.last_login_at or '-'}"
        )


@click.command("set-password")
@click.argument("username")
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
@with_appcontext
def set_password(username: str, password: str):
    """Reset a user's password (offline recovery)."""
    user = User.query.filter(db.func.lower(User.username) == username.lower()).first()
    if not user:
        raise click.ClickException(f"Usuário '{username}' não encontrado.")
    user.set_password(password)
    db.session.commit()
    click.secho(f"Senha de '{user.username}' redefinida.", fg="green")


@click.command("backup")
@click.option("--scope", type=click.Choice(["user", "full"]), default="full", show_default=True)
@click.option("--username", default=None, help="Obrigatório quando --scope=user")
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path), default=None)
@with_appcontext
def backup_cmd(scope: str, username: str | None, output: Path | None):
    """Write a JSON backup to disk (stdout-safe, cron friendly)."""
    from .backup import service

    if scope == "user":
        if not username:
            raise click.ClickException("Informe --username ao exportar o escopo 'user'.")
        user = User.query.filter(db.func.lower(User.username) == username.lower()).first()
        if not user:
            raise click.ClickException(f"Usuário '{username}' não encontrado.")
        payload = service.export_tasks(user)
    else:
        payload = service.export_full()

    if output is None:
        target = service.backup_dir() / service.snapshot_name(scope, None if scope == "full" else user)
    else:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)

    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    click.secho(f"Backup gravado em {target}", fg="green")
    click.echo(json.dumps(payload["counts"]))


@click.command("restore")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--mode", type=click.Choice(["merge", "replace"]), default="merge", show_default=True)
@click.option("--username", default=None, help="Usuário destino quando o backup é de escopo 'user'")
@with_appcontext
def restore_cmd(path: Path, mode: str, username: str | None):
    """Restore a JSON backup from disk."""
    from .backup import service

    payload = service.payload_from_text(path.read_text(encoding="utf-8"))
    scope = (payload.get("scope") or "user").lower()

    if scope == "full":
        stats = service.restore_full(payload, mode=mode)
    else:
        if not username:
            raise click.ClickException("Informe --username para restaurar um backup de usuário.")
        user = User.query.filter(db.func.lower(User.username) == username.lower()).first()
        if not user:
            raise click.ClickException(f"Usuário '{username}' não encontrado.")
        stats = service.restore_tasks(user, payload, mode=mode)

    click.secho(f"Restauração concluída ({scope}, modo={mode}).", fg="green")
    click.echo(json.dumps(stats))


@click.command("snapshots")
@click.option("--scope", type=click.Choice(["all", "user", "full"]), default="all", show_default=True)
@with_appcontext
def snapshots_cmd(scope: str):
    """List server-side backup snapshots."""
    from .backup import service

    rows = service.list_snapshots(scope)
    if not rows:
        click.echo("Nenhum snapshot encontrado.")
        return
    for row in rows:
        click.echo(f"{row['modified']:%Y-%m-%d %H:%M}  {row['size_human']:>9}  {row['name']}")


@click.command("purge-completed")
@click.option("--username", default=None, help="Restringe a um usuário")
@click.option("--days", default=30, show_default=True, help="Remove concluídas há mais de N dias")
@with_appcontext
def purge_completed(username: str | None, days: int):
    """Delete completed/cancelled tasks older than N days."""
    cutoff = now_local() - timedelta(days=days)
    query = Task.query.filter(Task.status.in_(["done", "cancelled"]), Task.updated_at < cutoff)
    if username:
        user = User.query.filter(db.func.lower(User.username) == username.lower()).first()
        if not user:
            raise click.ClickException(f"Usuário '{username}' não encontrado.")
        query = query.filter(Task.user_id == user.id)
    count = query.count()
    for task in query.all():
        db.session.delete(task)
    db.session.commit()
    click.secho(f"{count} tarefa(s) removida(s).", fg="green")


@click.command("seed-demo")
@click.argument("username")
@with_appcontext
def seed_demo(username: str):
    """Populate an account with example BUJO entries (homelab demo)."""
    user = User.query.filter(db.func.lower(User.username) == username.lower()).first()
    if not user:
        raise click.ClickException(f"Usuário '{username}' não encontrado.")

    today = today_local()
    samples = [
        ("Revisar o backup do homelab", "task", "high", 0, None),
        ("Planejar a semana no journal", "task", "medium", 0, None),
        ("Reunião de alinhamento", "event", "none", 0, 0),
        ("Ideia: dashboard de métricas", "note", "low", 0, None),
        ("Pagar a fatura do servidor", "task", "high", 1, 3),
        ("Trocar o SSD do NAS", "task", "medium", 5, 12),
        ("Aniversário do João", "event", "low", 9, 9),
        ("Ler 30 páginas", "task", "low", 2, None),
    ]
    created = 0
    for index, (title, kind, priority, offset, due_offset) in enumerate(samples):
        task = Task(
            user_id=user.id,
            title=title,
            kind=kind,
            priority=priority,
            scheduled_date=today + timedelta(days=offset),
            due_date=today + timedelta(days=due_offset) if due_offset is not None else None,
            position=index,
        )
        db.session.add(task)
        created += 1
    db.session.commit()
    click.secho(f"{created} tarefa(s) de exemplo criada(s) para '{user.username}'.", fg="green")


@click.command("dedupe-tasks")
@click.option("--username", default=None, help="Restringe a um usuário")
@click.option(
    "--across-days",
    is_flag=True,
    help="Também remove artefatos da migração antiga que ficaram em dias diferentes",
)
@click.option("--apply", "apply_changes", is_flag=True, help="Remove de fato (sem isso, só mostra o plano)")
@with_appcontext
def dedupe_tasks(username: str | None, across_days: bool, apply_changes: bool):
    """Remove tarefas duplicadas criadas pela migração antiga.

    * mesmo dia: mesmo usuário, título, data e situação (o bug copiava a tarefa
      a cada migração);
    * ``--across-days``: pares ligados por ``migrated_from_id`` ou uma entrada
      já concluída/cancelada que ficou atrás de outra aberta com o mesmo nome.
      Tarefas repetidas legítimas (um hábito diário, por exemplo) são
      preservadas: sem ligação e ambas abertas, nada é removido.

    Rode sem ``--apply`` primeiro para revisar o plano.
    """
    query = Task.query
    if username:
        user = User.query.filter(db.func.lower(User.username) == username.lower()).first()
        if not user:
            raise click.ClickException(f"Usuário '{username}' não encontrado.")
        query = query.filter(Task.user_id == user.id)

    tarefas = query.order_by(Task.id).all()
    duplicadas: list[tuple[Task, Task, str]] = []

    # ---- 1) duplicatas exatas no mesmo dia ----
    grupos: dict[tuple, list[Task]] = {}
    for task in tarefas:
        chave = (task.user_id, (task.title or "").strip().lower(), task.scheduled_date, task.status)
        grupos.setdefault(chave, []).append(task)
    for itens in grupos.values():
        if len(itens) < 2:
            continue
        manter, *resto = itens
        for dup in resto:
            duplicadas.append((manter, dup, "mesmo dia"))

    # ---- 2) artefatos da migração-cópia que sobraram em dias diferentes ----
    if across_days:
        por_titulo: dict[tuple, list[Task]] = {}
        for task in tarefas:
            por_titulo.setdefault((task.user_id, (task.title or "").strip().lower()), []).append(task)

        ja_marcadas = {id(dup) for _, dup, _ in duplicadas}
        for itens in por_titulo.values():
            if len(itens) < 2:
                continue
            abertas = [t for t in itens if t.status == STATUS_OPEN]
            if not abertas:
                continue
            manter = max(abertas, key=lambda t: (t.scheduled_date or date.min, t.id))
            ids = {t.id for t in itens}
            for task in itens:
                if task.id == manter.id or id(task) in ja_marcadas:
                    continue
                ligada = task.migrated_from_id in ids or any(o.migrated_from_id == task.id for o in itens)
                fechada = task.status in {STATUS_DONE, STATUS_CANCELLED}
                anterior = (task.scheduled_date or date.min) <= (manter.scheduled_date or date.min)
                if anterior and (ligada or fechada):
                    duplicadas.append((manter, task, "artefato de migração"))
                    ja_marcadas.add(id(task))

    if not duplicadas:
        click.secho("Nenhuma duplicata encontrada.", fg="green")
        return

    click.echo(f"{len(duplicadas)} duplicata(s) encontrada(s):")
    for manter, dup, motivo in duplicadas:
        click.echo(
            f"  mantém #{manter.id} e remove #{dup.id} ({motivo}) — '{manter.title}' "
            f"[{dup.scheduled_date}, {dup.status}, uuid {dup.uuid[:8]}]"
        )

    if not apply_changes:
        click.secho("Nada foi alterado. Rode com --apply para remover.", fg="yellow")
        return

    for manter, dup, _ in duplicadas:
        manter.migration_count = (manter.migration_count or 0) + (dup.migration_count or 0)
        db.session.delete(dup)
    db.session.commit()
    click.secho(f"{len(duplicadas)} duplicata(s) removida(s).", fg="green")


def main() -> int:  # pragma: no cover - convenience entry point
    """Allow ``python -m app.cli`` to print help."""
    click.echo("Use 'flask --app wsgi <comando>'. Veja 'flask --app wsgi --help'.", err=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
