"""Authentication, registration and user administration."""

from __future__ import annotations

from app.extensions import db
from app.models import Task, User

from .conftest import login, make_user


def test_login_page_renders(client):
    response = client.get("/auth/login")
    assert response.status_code == 200
    assert b"Entrar" in response.data


def test_login_page_offers_account_creation(client):
    """A tela de login traz a aba de cadastro com o formulário completo."""
    body = client.get("/auth/login").data
    assert b'id="tab-register"' in body
    assert b"Criar conta" in body
    assert b"/auth/register" in body
    # Campos do formulário de cadastro estão na própria tela de login.
    assert b'name="password2"' in body
    assert b'id="reg-username"' in body
    assert b'id="reg-password"' in body
    # A aba de entrar vem selecionada por padrão.
    assert b'class="nav-link active" id="tab-login"' in body


def test_register_from_login_tab_creates_account_and_logs_in(client, app):
    response = client.post(
        "/auth/register",
        data={
            "origin": "login",
            "username": "novato",
            "display_name": "Novato",
            "email": "",
            "password": "senha12345",
            "password2": "senha12345",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Registro di" in response.data  # caiu no dashboard (logado)
    user = User.query.filter_by(username="novato").one()
    assert user.check_password("senha12345")
    assert user.display_name == "Novato"


def test_register_from_login_tab_shows_errors_in_place(client, app):
    response = client.post(
        "/auth/register",
        data={
            "origin": "login",
            "username": "ab",  # curto demais
            "password": "123",
            "password2": "123",
        },
    )
    assert response.status_code == 200
    body = response.data
    assert b"Criar conta" in body
    assert b"3 a 64 caracteres" in body
    assert b"ao menos 8 caracteres" in body
    # A aba de cadastro continua aberta, com o formulário preenchido.
    assert b'class="nav-link active" id="tab-register"' in body
    assert b'value="ab"' in body
    assert User.query.filter_by(username="ab").first() is None


def test_register_from_login_tab_rejects_duplicate_username(client, app, user):
    response = client.post(
        "/auth/register",
        data={
            "origin": "login",
            "username": "ana",
            "password": "senha12345",
            "password2": "senha12345",
        },
    )
    assert response.status_code == 200
    assert "já existe".encode() in response.data
    assert User.query.filter_by(username="ana").count() == 1


def test_login_tab_shows_closed_notice_when_registration_disabled(client, app, user):
    app.config["ALLOW_REGISTRATION"] = "no"
    body = client.get("/auth/login").data
    assert b"Criar conta" in body  # a opção continua visível
    assert b"nav-link disabled" in body  # mas desabilitada
    assert b"cadastro est" in body.lower()
    assert b'name="password2"' not in body


def test_first_registered_user_becomes_admin(client, app):
    response = client.post(
        "/auth/register",
        data={
            "username": "primeiro",
            "display_name": "Primeiro",
            "email": "",
            "password": "senha12345",
            "password2": "senha12345",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    user = User.query.filter_by(username="primeiro").one()
    assert user.is_admin is True
    assert user.check_password("senha12345")


def test_register_from_login_tab_accepts_internal_email(client, app):
    """E-mail de domínio interno (comum em homelab) não pode ser recusado."""
    client.post(
        "/auth/register",
        data={
            "origin": "login",
            "username": "dono",
            "display_name": "Dono da casa",
            "email": "dono@casa.local",
            "password": "senha12345",
            "password2": "senha12345",
        },
        follow_redirects=True,
    )
    user = User.query.filter_by(username="dono").one()
    assert user.email == "dono@casa.local"


def test_register_from_login_tab_rejects_malformed_email(client, app):
    response = client.post(
        "/auth/register",
        data={
            "origin": "login",
            "username": "sememail",
            "email": "isso-nao-e-email",
            "password": "senha12345",
            "password2": "senha12345",
        },
    )
    assert response.status_code == 200
    assert "E-mail inválido".encode() in response.data
    assert User.query.filter_by(username="sememail").first() is None


def test_profile_accepts_internal_email(auth_client, app, user):
    auth_client.post(
        "/auth/profile",
        data={"display_name": "Ana", "email": "ana@homelab.local", "theme": "light", "submit": "Salvar perfil"},
        follow_redirects=True,
    )
    assert db.session.get(User, user.id).email == "ana@homelab.local"


def test_second_user_is_not_admin(client, app):
    make_user("existente")
    client.post(
        "/auth/register",
        data={"username": "segundo", "password": "senha12345", "password2": "senha12345"},
        follow_redirects=True,
    )
    assert User.query.filter_by(username="segundo").one().is_admin is False


def test_login_rejects_wrong_password(client, user):
    response = login(client, "ana", "errada")
    assert response.status_code == 200
    assert b"inv" in response.data.lower()


def test_login_and_logout_flow(client, user):
    response = login(client)
    assert response.status_code == 200

    response = client.post("/auth/logout", follow_redirects=True)
    assert response.status_code == 200
    # Depois do logout, a área logada redireciona para o login.
    protected = client.get("/", follow_redirects=False)
    assert protected.status_code == 302
    assert "/auth/login" in protected.headers["Location"]


def test_inactive_user_cannot_login(client, app):
    make_user("bloqueado", is_active_flag=False)
    response = login(client, "bloqueado")
    assert b"desativada" in response.data


def test_dashboard_requires_login(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_registration_can_be_disabled(client, app, user):
    app.config["ALLOW_REGISTRATION"] = "no"
    response = client.get("/auth/register", follow_redirects=True)
    assert b"desativado" in response.data


def test_profile_update_and_password_change(auth_client, app, user):
    response = auth_client.post(
        "/auth/profile",
        data={"display_name": "Ana Maria", "email": "ana@example.com", "theme": "dark", "submit": "Salvar perfil"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    refreshed = db.session.get(User, user.id)
    assert refreshed.display_name == "Ana Maria"
    assert refreshed.theme == "dark"

    auth_client.post(
        "/auth/profile/password",
        data={"current_password": "senha12345", "password": "novasenha123", "password2": "novasenha123"},
        follow_redirects=True,
    )
    assert db.session.get(User, user.id).check_password("novasenha123")


def test_profile_rejects_wrong_current_password(auth_client, user):
    auth_client.post(
        "/auth/profile/password",
        data={"current_password": "errada", "password": "novasenha123", "password2": "novasenha123"},
        follow_redirects=True,
    )
    assert db.session.get(User, user.id).check_password("senha12345")


def test_non_admin_cannot_open_user_admin(auth_client):
    assert auth_client.get("/auth/users").status_code == 403


def test_admin_can_manage_users(client, admin, app):
    login(client, "root", "senha12345")
    assert client.get("/auth/users").status_code == 200

    response = client.post(
        "/auth/users/create",
        data={"username": "novo", "password": "senha12345", "is_admin": ""},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert User.query.filter_by(username="novo").one() is not None

    created = User.query.filter_by(username="novo").one()
    client.post(f"/auth/users/{created.id}/toggle-admin", follow_redirects=True)
    assert db.session.get(User, created.id).is_admin is True

    client.post(f"/auth/users/{created.id}/toggle-active", follow_redirects=True)
    assert db.session.get(User, created.id).is_active is False

    client.post(f"/auth/users/{created.id}/delete", follow_redirects=True)
    assert db.session.get(User, created.id) is None


def test_admin_cannot_delete_self(client, admin):
    login(client, "root", "senha12345")
    client.post(f"/auth/users/{admin.id}/delete", follow_redirects=True)
    assert db.session.get(User, admin.id) is not None


def test_users_are_isolated(auth_client, client, app, user):
    """Uma tarefa de outro usuário nunca deve aparecer."""
    other = make_user("bia")
    task = Task(user_id=other.id, title="Segredo da Bia", scheduled_date=__import__("datetime").date.today())
    db.session.add(task)
    db.session.commit()

    assert auth_client.get(f"/tasks/{task.id}").status_code == 404
    assert auth_client.post(f"/tasks/{task.id}/delete").status_code == 404
    assert b"Segredo da Bia" not in auth_client.get("/tasks/").data
