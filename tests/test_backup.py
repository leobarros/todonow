"""Backup export, restore (merge/replace), snapshots and CLI helpers."""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.backup import service
from app.extensions import db
from app.models import Task, User

from .conftest import login, make_user


def _export_user(client):
    response = client.get("/backup/export?scope=user")
    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]
    return json.loads(response.data)


def test_export_user_scope(auth_client, app, user, sample_tasks):
    payload = _export_user(auth_client)
    assert payload["format"] == "todonow.backup"
    assert payload["scope"] == "user"
    assert payload["counts"]["tasks"] == len(sample_tasks)
    assert {t["title"] for t in payload["tasks"]} == {t.title for t in sample_tasks}


def test_export_full_requires_admin(auth_client):
    assert auth_client.get("/backup/export?scope=full").status_code == 403


def test_full_export_includes_password_hash(client, admin, app):
    login(client, "root", "senha12345")
    response = client.get("/backup/export?scope=full")
    assert response.status_code == 200
    payload = json.loads(response.data)
    assert payload["scope"] == "full"
    assert payload["users"][0]["password_hash"].startswith(("scrypt:", "pbkdf2:"))


def test_import_merge_restores_deleted_tasks(auth_client, app, user, sample_tasks):
    payload = _export_user(auth_client)
    for task in Task.for_user(user).all():
        db.session.delete(task)
    db.session.commit()
    assert Task.for_user(user).count() == 0

    response = auth_client.post(
        "/backup/import",
        data={"mode": "merge", "text": json.dumps(payload), "file": (None, "")},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"restaurado" in response.data.lower()
    assert Task.for_user(user).count() == len(sample_tasks)
    assert Task.for_user(user).filter_by(title="Tarefa de hoje").one().priority == "high"


def test_import_is_idempotent(auth_client, app, user, sample_tasks):
    payload = _export_user(auth_client)
    for _ in range(2):
        auth_client.post(
            "/backup/import",
            data={"mode": "merge", "text": json.dumps(payload)},
            follow_redirects=True,
        )
    assert Task.for_user(user).count() == len(sample_tasks)


def test_import_replace_removes_existing(auth_client, app, user, sample_tasks):
    payload = {
        "format": "todonow.backup",
        "scope": "user",
        "tasks": [
            {
                "uuid": "a" * 32,
                "title": "Única",
                "kind": "task",
                "status": "open",
                "scheduled_date": date.today().isoformat(),
            }
        ],
    }
    response = auth_client.post(
        "/backup/import",
        data={"mode": "replace", "text": json.dumps(payload)},
        follow_redirects=True,
    )
    assert response.status_code == 200
    titles = [t.title for t in Task.for_user(user).all()]
    assert titles == ["Única"]


def test_import_rejects_invalid_json(auth_client, app, user):
    response = auth_client.post(
        "/backup/import",
        data={"mode": "merge", "text": "{isso não é json}"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"JSON inv" in response.data


def test_import_requires_file_or_text(auth_client):
    response = auth_client.post("/backup/import", data={"mode": "merge"}, follow_redirects=True)
    assert response.status_code == 200
    assert b"Envie um arquivo" in response.data


def test_restore_updates_existing_task_by_uuid(auth_client, app, user, sample_tasks):
    payload = _export_user(auth_client)
    payload["tasks"][0]["title"] = "Título restaurado"
    payload["tasks"][0]["status"] = "done"

    auth_client.post("/backup/import", data={"mode": "merge", "text": json.dumps(payload)}, follow_redirects=True)
    restored = Task.for_user(user).filter(Task.uuid == payload["tasks"][0]["uuid"]).one()
    assert restored.title == "Título restaurado"
    assert restored.status == "done"
    assert restored.completed_at is not None


def test_full_restore_by_admin(client, admin, app):
    other = make_user("eva")
    db.session.add(Task(user_id=other.id, title="Tarefa da Eva", scheduled_date=date.today()))
    db.session.commit()

    login(client, "root", "senha12345")
    payload = json.loads(client.get("/backup/export?scope=full").data)

    # Apaga tudo e restaura do backup completo.
    for task in Task.query.all():
        db.session.delete(task)
    db.session.commit()

    response = client.post("/backup/import", data={"mode": "merge", "text": json.dumps(payload)}, follow_redirects=True)
    assert response.status_code == 200
    assert Task.query.count() == 1
    assert User.query.filter_by(username="eva").one() is not None


def test_non_admin_cannot_restore_full_payload(auth_client, app, user):
    payload = {"format": "todonow.backup", "scope": "full", "users": [{"username": "hacker", "tasks": []}]}
    response = auth_client.post(
        "/backup/import", data={"mode": "merge", "text": json.dumps(payload)}, follow_redirects=True
    )
    assert b"administradores" in response.data
    assert User.query.filter_by(username="hacker").first() is None


def test_snapshot_create_list_restore_delete(auth_client, app, user, sample_tasks):
    response = auth_client.post(
        "/backup/snapshot",
        data={"scope": "user", "label": "antes-da-limpeza"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Snapshot criado" in response.data

    rows = service.list_snapshots()
    assert len(rows) == 1
    name = rows[0]["name"]
    assert "antes-da-limpeza" in name

    # Zera as tarefas e restaura pelo snapshot.
    for task in Task.for_user(user).all():
        db.session.delete(task)
    db.session.commit()

    auth_client.post(f"/backup/snapshot/{name}/restore", data={"mode": "merge"}, follow_redirects=True)
    assert Task.for_user(user).count() == len(sample_tasks)

    download = auth_client.get(f"/backup/snapshot/{name}/download")
    assert download.status_code == 200
    assert json.loads(download.data)["scope"] == "user"

    auth_client.post(f"/backup/snapshot/{name}/delete", follow_redirects=True)
    assert service.list_snapshots() == []


def test_snapshot_unknown_name_redirects(auth_client):
    response = auth_client.get("/backup/snapshot/nao-existe.json/download", follow_redirects=True)
    assert response.status_code == 200
    assert b"n" in response.data  # mensagem de erro exibida


def test_snapshot_path_traversal_is_blocked(app, user):
    with pytest.raises(service.RestoreError):
        service.snapshot_path("../../../etc/passwd")


def test_user_cannot_open_another_users_snapshot(app, user, sample_tasks):
    other = make_user("fabio")
    payload = service.export_tasks(other)
    path = service.save_snapshot(payload, "user", other)

    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()
    login(client, "ana", "senha12345")
    response = client.get(f"/backup/snapshot/{path.name}/download")
    assert response.status_code == 403


def test_snapshot_pruning_keeps_newest(app, user, sample_tasks):
    payload = service.export_tasks(user)
    for _ in range(5):
        service.save_snapshot(payload, "user", user)
    service.prune_snapshots(scope="user", user=user, keep=2)
    assert len(service.list_snapshots()) == 2


def test_snapshot_name_is_sanitised(app, user):
    payload = service.export_tasks(user)
    path = service.save_snapshot(payload, "user", user)
    assert path.name.startswith("todonow-user-ana-")
    assert path.suffix == ".json"


def test_summary_reports_counts(app, user, sample_tasks):
    payload = service.export_tasks(user)
    info = service.summary(payload)
    assert info["scope"] == "user"
    assert info["tasks"] == len(sample_tasks)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_cli_backup_and_restore(app, runner, user, sample_tasks, tmp_path):
    output = tmp_path / "cli-backup.json"
    result = runner.invoke(args=["backup", "--scope", "user", "--username", "ana", "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert output.exists()

    for task in Task.for_user(user).all():
        db.session.delete(task)
    db.session.commit()

    result = runner.invoke(args=["restore", str(output), "--mode", "merge", "--username", "ana"])
    assert result.exit_code == 0, result.output
    assert Task.for_user(user).count() == len(sample_tasks)


def test_cli_create_user_and_list(app, runner):
    result = runner.invoke(
        args=["create-user", "cli-user", "--password", "senha12345", "--email", "cli@example.com", "--admin"]
    )
    assert result.exit_code == 0, result.output
    created = User.query.filter_by(username="cli-user").one()
    assert created.is_admin is True
    assert created.email == "cli@example.com"

    # --only-if-missing não altera a senha existente.
    result = runner.invoke(args=["create-user", "cli-user", "--password", "outrasenha1", "--only-if-missing"])
    assert result.exit_code == 0
    assert User.query.filter_by(username="cli-user").one().check_password("senha12345")

    result = runner.invoke(args=["list-users"])
    assert result.exit_code == 0
    assert "cli-user" in result.output


def test_cli_seed_demo_and_purge(app, runner, user):
    result = runner.invoke(args=["seed-demo", "ana"])
    assert result.exit_code == 0, result.output
    assert Task.for_user(user).count() > 0

    result = runner.invoke(args=["purge-completed", "--username", "ana", "--days", "0"])
    assert result.exit_code == 0, result.output


def test_cli_set_password(app, runner, user):
    result = runner.invoke(args=["set-password", "ana", "--password", "novasenha123"])
    assert result.exit_code == 0, result.output
    assert db.session.get(User, user.id).check_password("novasenha123")
