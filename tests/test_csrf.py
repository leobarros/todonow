"""Tratamento de falhas de CSRF (token ausente/expirado)."""

from __future__ import annotations

from app.models import Task

from .conftest import make_user


def test_expired_csrf_shows_friendly_message(client, app, user):
    """Sem token o usuário não vê a página de erro 400, e sim um aviso."""
    app.config["WTF_CSRF_ENABLED"] = True
    response = client.post(
        "/auth/login",
        data={"username": "ana", "password": "senha12345"},
        headers={"Referer": "http://localhost/auth/login"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    body = response.data
    assert "sessão expirou".encode() in body
    assert b"Requisi" not in body  # não é a tela de erro 400
    # E o login não aconteceu de fato.
    assert b"Registro di" not in body


def test_expired_csrf_returns_json_for_ajax(client, app, user):
    app.config["WTF_CSRF_ENABLED"] = True
    response = client.post(
        "/tasks/quick",
        data={"title": "qualquer"},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == 400
    assert "expirou" in payload["error"]


def test_expired_csrf_does_not_execute_action(auth_client, app, user):
    app.config["WTF_CSRF_ENABLED"] = True
    response = auth_client.post("/tasks/quick", data={"title": "Sem token"})
    assert response.status_code == 302
    assert Task.query.filter_by(title="Sem token").count() == 0


def test_expired_csrf_ignores_foreign_referrer(auth_client, app, user):
    """Não pode virar open redirect usando o cabeçalho Referer."""
    app.config["WTF_CSRF_ENABLED"] = True
    response = auth_client.post(
        "/tasks/quick",
        data={"title": "x"},
        headers={"Referer": "https://sitio-malicioso.example/roubar"},
    )
    assert response.status_code == 302
    assert "sitio-malicioso" not in response.headers["Location"]
    assert response.headers["Location"].startswith("/")


def test_valid_csrf_still_works(auth_client, app, user):
    """Com token válido nada muda: a ação acontece normalmente."""
    app.config["WTF_CSRF_ENABLED"] = True
    page = auth_client.get("/")
    token = _extract_token(page.data)
    assert token

    response = auth_client.post(
        "/tasks/quick",
        data={"csrf_token": token, "title": "Com token válido", "next": "/"},
    )
    assert response.status_code == 302
    assert Task.query.filter_by(title="Com token válido").count() == 1


def test_login_with_valid_csrf_works(client, app, user):
    app.config["WTF_CSRF_ENABLED"] = True
    page = client.get("/auth/login")
    token = _extract_token(page.data)
    assert token

    response = client.post(
        "/auth/login",
        data={"csrf_token": token, "username": "ana", "password": "senha12345"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Registro di" in response.data


def test_duplicate_usernames_are_case_insensitive(app, client):
    make_user("Maria")
    response = client.post(
        "/auth/register",
        data={"origin": "login", "username": "maria", "password": "senha12345", "password2": "senha12345"},
    )
    assert response.status_code == 200
    assert "já existe".encode() in response.data


def _extract_token(html: bytes) -> str:
    import re

    match = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
    return match.group(1).decode() if match else ""
