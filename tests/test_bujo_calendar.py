"""BUJO logs (daily/monthly/future) and the calendar feed/API."""

from __future__ import annotations

from datetime import date, timedelta

from app.extensions import db
from app.models import Task
from app.utils import month_matrix, week_days


def test_daily_log_shows_entries_and_marks(auth_client, app, user, sample_tasks):
    response = auth_client.get("/day")
    assert response.status_code == 200
    body = response.data
    assert b"Tarefa de hoje" in body
    assert b"Daily log" in body
    # Símbolos BUJO aparecem na legenda.
    assert "✕".encode() in body
    assert "•".encode() in body


def test_daily_log_navigates_to_specific_day(auth_client, app, user, sample_tasks):
    target = date.today() - timedelta(days=3)
    response = auth_client.get(f"/day/{target.isoformat()}")
    assert response.status_code == 200
    assert b"Nota antiga" in response.data


def test_daily_log_lists_entries_flat(auth_client, app, user, sample_tasks):
    """Sem coleções: as entradas do dia aparecem em lista simples."""
    body = auth_client.get("/day").data
    assert b"Tarefa de hoje" in body
    assert b"Sem cole" not in body
    assert b"collection-title" not in body


def test_monthly_log_lists_month(auth_client, app, user, sample_tasks):
    today = date.today()
    response = auth_client.get(f"/monthly/{today.year}/{today.month}")
    assert response.status_code == 200
    assert b"Monthly log" in response.data
    assert b"Tarefa de hoje" in response.data


def test_monthly_log_rejects_invalid_month(auth_client):
    assert auth_client.get("/monthly/2025/13").status_code == 404


def test_future_log_groups_by_month(auth_client, app, user):
    task = Task(user_id=user.id, title="Projeto do futuro", scheduled_date=date.today() + timedelta(days=45))
    db.session.add(task)
    db.session.commit()

    response = auth_client.get("/future")
    assert response.status_code == 200
    assert b"Projeto do futuro" in response.data
    assert b"Future log" in response.data


def test_calendar_page_renders(auth_client):
    response = auth_client.get("/calendar")
    assert response.status_code == 200
    assert b"FullCalendar" not in response.data  # o bundle é carregado via <script src>


def test_calendar_feed_returns_events(auth_client, app, user, sample_tasks):
    today = date.today()
    start = (today - timedelta(days=10)).isoformat()
    end = (today + timedelta(days=10)).isoformat()
    response = auth_client.get(f"/calendar/feed?start={start}&end={end}")
    assert response.status_code == 200
    events = response.get_json()
    titles = [event["title"] for event in events]
    assert any("Tarefa de hoje" in title for title in titles)
    assert all(event["allDay"] is True for event in events)
    assert all("classNames" in event for event in events)

    done_event = next(e for e in events if "Já concluída" in e["title"])
    assert "bujo-status-done" in done_event["classNames"]


def test_calendar_feed_emits_due_events(auth_client, app, user):
    task = Task(
        user_id=user.id,
        title="Com prazo",
        scheduled_date=date.today(),
        due_date=date.today() + timedelta(days=4),
    )
    db.session.add(task)
    db.session.commit()

    events = auth_client.get("/calendar/feed").get_json()
    due = [e for e in events if e["extendedProps"]["isDue"]]
    assert len(due) == 1
    assert due[0]["start"] == (date.today() + timedelta(days=4)).isoformat()
    assert due[0]["editable"] is False


def test_calendar_move_reschedules(auth_client, app, user):
    task = Task(user_id=user.id, title="Arrastar", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    target = date.today() + timedelta(days=6)
    response = auth_client.post(
        "/calendar/move",
        json={"id": task.id, "date": target.isoformat()},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert db.session.get(Task, task.id).scheduled_date == target


def test_calendar_move_rejects_invalid_payload(auth_client, app, user):
    task = Task(user_id=user.id, title="X", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    response = auth_client.post(
        "/calendar/move", json={"id": task.id, "date": "não-é-data"}, headers={"Accept": "application/json"}
    )
    assert response.status_code == 400
    assert response.get_json()["ok"] is False


def test_calendar_move_blocks_other_users_task(auth_client, app, user):
    from .conftest import make_user

    other = make_user("duda")
    task = Task(user_id=other.id, title="Dela", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    response = auth_client.post(
        "/calendar/move",
        json={"id": task.id, "date": date.today().isoformat()},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 404


def test_theme_toggle_persists(auth_client, app, user):
    response = auth_client.post("/theme", json={"theme": "dark"}, headers={"Accept": "application/json"})
    assert response.status_code == 200
    assert response.get_json()["theme"] == "dark"
    assert db.session.get(type(user), user.id).theme == "dark"


def test_month_matrix_covers_whole_month():
    weeks = month_matrix(2025, 10, week_start=6)
    flat = [day for week in weeks for day in week]
    assert date(2025, 10, 1) in flat
    assert date(2025, 10, 31) in flat
    assert len(weeks[0]) == 7
    # Domingo como primeiro dia da semana.
    assert weeks[0][0].weekday() == 6


def test_week_days_respects_week_start():
    days = week_days(date(2025, 10, 10), week_start=6)
    assert len(days) == 7
    assert days[0].weekday() == 6
