"""Task CRUD, completion, bulk actions and scheduling rules."""

from __future__ import annotations

from datetime import date, timedelta

from app.extensions import db
from app.models import Task

from .conftest import make_user


def test_quick_add_starts_today(auth_client, app, user):
    response = auth_client.post(
        "/tasks/quick",
        data={"title": "Comprar café", "kind": "task", "priority": "high", "next": "/"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    task = Task.query.filter_by(title="Comprar café").one()
    assert task.scheduled_date == date.today()
    assert task.status == "open"
    assert task.priority == "high"


def test_quick_add_accepts_future_date(auth_client, user):
    future = date.today() + timedelta(days=20)
    auth_client.post(
        "/tasks/quick",
        data={"title": "Renovar domínio", "scheduled_date": future.isoformat()},
        follow_redirects=True,
    )
    task = Task.query.filter_by(title="Renovar domínio").one()
    assert task.scheduled_date == future
    assert task.is_future is True


def test_create_form_defaults_to_today(auth_client, user):
    response = auth_client.get("/tasks/new")
    assert response.status_code == 200
    assert date.today().isoformat().encode() in response.data


def test_create_full_task_with_due_date(auth_client, user):
    scheduled = date.today() + timedelta(days=2)
    due = date.today() + timedelta(days=9)
    auth_client.post(
        "/tasks/new",
        data={
            "title": "Entregar relatório",
            "notes": "Com gráficos",
            "kind": "task",
            "status": "open",
            "priority": "medium",
            "collection": "Trabalho",
            "scheduled_date": scheduled.isoformat(),
            "due_date": due.isoformat(),
        },
        follow_redirects=True,
    )
    task = Task.query.filter_by(title="Entregar relatório").one()
    assert task.scheduled_date == scheduled
    assert task.due_date == due
    assert task.collection == "Trabalho"
    assert task.notes == "Com gráficos"


def test_due_date_before_schedule_is_rejected(auth_client, user):
    today = date.today()
    response = auth_client.post(
        "/tasks/new",
        data={
            "title": "Data errada",
            "kind": "task",
            "status": "open",
            "priority": "none",
            "scheduled_date": (today + timedelta(days=5)).isoformat(),
            "due_date": today.isoformat(),
        },
    )
    assert response.status_code == 200
    assert b"anterior" in response.data
    assert Task.query.filter_by(title="Data errada").count() == 0


def test_toggle_strikes_through_and_reopens(auth_client, app, user):
    task = Task(user_id=user.id, title="Riscar isso", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    auth_client.post(f"/tasks/{task.id}/toggle", follow_redirects=True)
    refreshed = db.session.get(Task, task.id)
    assert refreshed.status == "done"
    assert refreshed.completed_at is not None
    assert refreshed.is_strikethrough is True
    assert refreshed.bujo_symbol == "✕"

    auth_client.post(f"/tasks/{task.id}/toggle", follow_redirects=True)
    refreshed = db.session.get(Task, task.id)
    assert refreshed.status == "open"
    assert refreshed.completed_at is None


def test_toggle_returns_json_for_ajax(auth_client, app, user):
    task = Task(user_id=user.id, title="Ajax", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    response = auth_client.post(
        f"/tasks/{task.id}/toggle",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["task"]["status"] == "done"
    assert payload["task"]["bujo_symbol"] == "✕"


def test_edit_task(auth_client, app, user):
    task = Task(user_id=user.id, title="Antigo", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    new_date = date.today() + timedelta(days=3)
    auth_client.post(
        f"/tasks/{task.id}/edit",
        data={
            "title": "Novo título",
            "kind": "event",
            "status": "open",
            "priority": "low",
            "collection": "",
            "scheduled_date": new_date.isoformat(),
            "due_date": "",
            "notes": "",
        },
        follow_redirects=True,
    )
    refreshed = db.session.get(Task, task.id)
    assert refreshed.title == "Novo título"
    assert refreshed.kind == "event"
    assert refreshed.scheduled_date == new_date


def test_delete_task(auth_client, app, user):
    task = Task(user_id=user.id, title="Morrer", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()
    task_id = task.id

    auth_client.post(f"/tasks/{task_id}/delete", follow_redirects=True)
    assert db.session.get(Task, task_id) is None


def test_bulk_complete(auth_client, app, user):
    tasks = [Task(user_id=user.id, title=f"T{i}", scheduled_date=date.today()) for i in range(3)]
    db.session.add_all(tasks)
    db.session.commit()

    auth_client.post(
        "/tasks/bulk",
        data={"action": "complete", "ids": [str(t.id) for t in tasks]},
        follow_redirects=True,
    )
    assert Task.for_user(user).filter(Task.status == "done").count() == 3


def test_bulk_migrate_tomorrow(auth_client, app, user):
    task = Task(user_id=user.id, title="Rolar", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    auth_client.post("/tasks/bulk", data={"action": "migrate_tomorrow", "ids": [str(task.id)]}, follow_redirects=True)
    original = db.session.get(Task, task.id)
    assert original.status == "migrated"
    assert original.migrated_to_date == date.today() + timedelta(days=1)
    successor = Task.for_user(user).filter(Task.migrated_from_id == task.id).one()
    assert successor.scheduled_date == date.today() + timedelta(days=1)


def test_delete_completed(auth_client, app, user):
    done = Task(user_id=user.id, title="Concluída", scheduled_date=date.today(), status="done")
    open_task = Task(user_id=user.id, title="Aberta", scheduled_date=date.today())
    db.session.add_all([done, open_task])
    db.session.commit()

    auth_client.post("/tasks/delete-completed", follow_redirects=True)
    remaining = Task.for_user(user).all()
    assert [t.title for t in remaining] == ["Aberta"]


def test_task_list_filters(auth_client, app, user, sample_tasks):
    response = auth_client.get("/tasks/?q=Evento")
    assert b"Evento de amanh" in response.data
    assert b"Nota antiga" not in response.data

    response = auth_client.get("/tasks/?kind=note")
    assert b"Nota antiga" in response.data
    assert b"Evento de amanh" not in response.data

    response = auth_client.get("/tasks/?collection=Trabalho")
    assert b"Tarefa de hoje" in response.data
    assert b"Nota antiga" not in response.data

    response = auth_client.get("/tasks/?when=future")
    assert b"Evento de amanh" in response.data
    assert b"Tarefa de hoje" not in response.data


def test_stats_reflect_status(app, user, sample_tasks):
    stats = Task.stats(user)
    assert stats["total"] == 4
    assert stats["done"] == 1
    assert stats["open"] == 3
    assert stats["overdue"] == 1
    assert stats["future"] == 1


def test_migrate_endpoint_creates_successor(auth_client, app, user):
    task = Task(user_id=user.id, title="Migrar", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    auth_client.post(f"/tasks/{task.id}/migrate", data={"preset": "week"}, follow_redirects=True)
    assert db.session.get(Task, task.id).migrated_to_date == date.today() + timedelta(days=7)
    successor = Task.for_user(user).filter(Task.migrated_from_id == task.id).one()
    assert successor.scheduled_date == date.today() + timedelta(days=7)
    assert successor.bujo_symbol == "•"


def test_other_users_task_is_not_accessible(auth_client, app, user):
    other = make_user("carla")
    task = Task(user_id=other.id, title="Dela", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    assert auth_client.get(f"/tasks/{task.id}/edit").status_code == 404
    assert auth_client.post(f"/tasks/{task.id}/toggle").status_code == 404
    assert auth_client.post(f"/tasks/{task.id}/migrate").status_code == 404
