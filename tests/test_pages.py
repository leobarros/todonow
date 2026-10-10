"""Smoke tests: every page renders, static assets are served, health works."""

from __future__ import annotations

from datetime import date

import pytest

from app.extensions import db
from app.models import Task

from .conftest import login

PAGES = [
    "/",
    "/day",
    "/monthly",
    "/future",
    "/calendar",
    "/tasks/",
    "/tasks/new",
    "/backup/",
    "/auth/profile",
]

ADMIN_PAGES = ["/auth/users"]


@pytest.mark.parametrize("path", PAGES)
def test_logged_in_pages_render(auth_client, path):
    response = auth_client.get(path)
    assert response.status_code == 200, path
    assert b"<html" in response.data


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_admin_pages_render(client, admin, path):
    login(client, "root", "senha12345")
    response = client.get(path)
    assert response.status_code == 200, path


def test_health_endpoint(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["database"] is True


def test_task_detail_and_edit_render(auth_client, app, user):
    task = Task(user_id=user.id, title="Detalhes", notes="linha 1\nlinha 2", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    detail = auth_client.get(f"/tasks/{task.id}")
    assert detail.status_code == 200
    assert b"Detalhes" in detail.data
    assert b"linha 1" in detail.data

    edit = auth_client.get(f"/tasks/{task.id}/edit")
    assert edit.status_code == 200


def test_static_assets_are_vendored(auth_client):
    assets = [
        "/static/css/todonow.css",
        "/static/js/todonow.js",
        "/static/js/calendar.js",
        "/static/vendor/bootstrap/bootstrap.min.css",
        "/static/vendor/bootstrap/bootstrap.bundle.min.js",
        "/static/vendor/bootstrap-icons/bootstrap-icons.min.css",
        "/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2",
        "/static/vendor/fullcalendar.global.min.js",
        "/static/vendor/fullcalendar-locale-pt-br.global.min.js",
    ]
    for asset in assets:
        response = auth_client.get(asset)
        assert response.status_code == 200, asset
        assert response.data, asset


def test_404_uses_custom_page(auth_client):
    response = auth_client.get("/nao-existe")
    assert response.status_code == 404
    assert b"404" in response.data


def test_404_returns_json_for_api(auth_client):
    response = auth_client.get("/api/nao-existe", headers={"Accept": "application/json"})
    assert response.status_code == 404
    assert response.get_json()["status"] == 404


def test_login_page_has_no_navbar_for_anonymous(client):
    response = client.get("/auth/login")
    assert b"navbar" not in response.data


def test_security_headers_present(auth_client):
    response = auth_client.get("/")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]


def test_dashboard_shows_today_and_upcoming(auth_client, app, user, sample_tasks):
    response = auth_client.get("/")
    body = response.data
    assert b"Tarefa de hoje" in body
    assert b"Pr" in body  # "Próximos 7 dias"
    assert "Registro diário".encode() in body


def test_dashboard_marks_today_tasks_done(auth_client, app, user):
    task = Task(user_id=user.id, title="Riscada", scheduled_date=date.today(), status="done")
    task.complete()
    db.session.add(task)
    db.session.commit()

    body = auth_client.get("/").data
    assert b"is-done" in body
    assert b"Riscada" in body
