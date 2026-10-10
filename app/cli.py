"""Flask CLI commands — user management, backups and maintenance."""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import click
from flask.cli import with_appcontext

from .extensions import db
from .models import Task, User
from .utils import now_local, today_local


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
        ("Revisar o backup do homelab", "task", "high", "Infra", 0, None),
        ("Planejar a semana no journal", "task", "medium", "Pessoal", 0, None),
        ("Reunião de alinhamento", "event", "none", "Trabalho", 0, 0),
        ("Ideia: dashboard de métricas", "note", "low", "Projetos", 0, None),
        ("Pagar a fatura do servidor", "task", "high", "Finanças", 1, 3),
        ("Trocar o SSD do NAS", "task", "medium", "Infra", 5, 12),
        ("Aniversário do João", "event", "low", "Pessoal", 9, 9),
        ("Ler 30 páginas", "task", "low", "Pessoal", 2, None),
    ]
    created = 0
    for index, (title, kind, priority, collection, offset, due_offset) in enumerate(samples):
        task = Task(
            user_id=user.id,
            title=title,
            kind=kind,
            priority=priority,
            collection=collection,
            scheduled_date=today + timedelta(days=offset),
            due_date=today + timedelta(days=due_offset) if due_offset is not None else None,
            position=index,
        )
        db.session.add(task)
        created += 1
    db.session.commit()
    click.secho(f"{created} tarefa(s) de exemplo criada(s) para '{user.username}'.", fg="green")


def main() -> int:  # pragma: no cover - convenience entry point
    """Allow ``python -m app.cli`` to print help."""
    click.echo("Use 'flask --app wsgi <comando>'. Veja 'flask --app wsgi --help'.", err=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
