"""Shared pytest fixtures."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app import create_app
from app.extensions import db as _db
from app.models import Task, User


@pytest.fixture()
def app(tmp_path):
    database = tmp_path / "test.db"
    app = create_app(
        "testing",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{database}",
        BACKUP_DIR=tmp_path / "backups",
        SECRET_KEY="test-secret",
        WTF_CSRF_ENABLED=False,
        SERVER_NAME=None,
    )
    with app.app_context():
        _db.create_all()
        yield app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def runner(app):
    return app.test_cli_runner()


def make_user(username="ana", password="senha12345", admin=False, **kwargs) -> User:
    user = User(username=username, display_name=kwargs.pop("display_name", username.title()), **kwargs)
    user.set_password(password)
    user.is_admin = admin
    _db.session.add(user)
    _db.session.commit()
    return user


@pytest.fixture()
def user(app):
    return make_user()


@pytest.fixture()
def admin(app):
    return make_user("root", "senha12345", admin=True, display_name="Root")


def login(client, username="ana", password="senha12345"):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


@pytest.fixture()
def auth_client(client, user):
    login(client)
    return client


@pytest.fixture()
def sample_tasks(app, user):
    today = date.today()
    tasks = [
        Task(
            user_id=user.id,
            title="Tarefa de hoje",
            kind="task",
            priority="high",
            collection="Trabalho",
            scheduled_date=today,
            position=1,
        ),
        Task(user_id=user.id, title="Evento de amanhã", kind="event", scheduled_date=today + timedelta(days=1)),
        Task(user_id=user.id, title="Nota antiga", kind="note", scheduled_date=today - timedelta(days=3)),
        Task(user_id=user.id, title="Já concluída", scheduled_date=today, status="done"),
    ]
    tasks[3].complete()
    _db.session.add_all(tasks)
    _db.session.commit()
    return tasks
