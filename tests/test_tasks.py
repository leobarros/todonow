"""Task CRUD, completion, bulk actions and scheduling rules."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

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
            "scheduled_date": scheduled.isoformat(),
            "due_date": due.isoformat(),
        },
        follow_redirects=True,
    )
    task = Task.query.filter_by(title="Entregar relatório").one()
    assert task.scheduled_date == scheduled
    assert task.due_date == due
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


def test_migrate_moves_task_without_duplicating(auth_client, app, user):
    """Migrar move a mesma tarefa: não cria cópia e não conclui."""
    task = Task(user_id=user.id, title="Rolar", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()
    antes = Task.for_user(user).count()

    auth_client.post(f"/tasks/{task.id}/migrate", data={"preset": "tomorrow"}, follow_redirects=True)

    assert Task.for_user(user).count() == antes  # nenhuma cópia criada
    movida = db.session.get(Task, task.id)
    assert movida.scheduled_date == date.today() + timedelta(days=1)
    assert movida.status == "open"  # migrar não é concluir
    assert movida.migration_count == 1
    assert movida.migrated_at is not None


def test_migrating_repeatedly_keeps_a_single_task(auth_client, app, user):
    """Regressão: migrar várias vezes gerava cópias no mesmo dia."""
    task = Task(user_id=user.id, title="Rolar", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    for dias in (1, 2, 3):
        auth_client.post(
            f"/tasks/{task.id}/migrate",
            data={"target_date": (date.today() + timedelta(days=dias)).isoformat()},
            follow_redirects=True,
        )

    assert Task.for_user(user).filter_by(title="Rolar").count() == 1
    movida = db.session.get(Task, task.id)
    assert movida.migration_count == 3
    assert movida.scheduled_date == date.today() + timedelta(days=3)


def test_migrate_preset_is_relative_to_the_task(auth_client, app, user):
    """Adiar usa a data da própria tarefa — não "hoje + 1"."""
    amanha = date.today() + timedelta(days=1)
    task = Task(user_id=user.id, title="Já é amanhã", scheduled_date=amanha)
    db.session.add(task)
    db.session.commit()

    auth_client.post(f"/tasks/{task.id}/migrate", data={"preset": "next_day"}, follow_redirects=True)

    movida = db.session.get(Task, task.id)
    assert movida.scheduled_date == amanha + timedelta(days=1)  # e não voltou para amanhã
    assert movida.migration_count == 1


def test_migrate_refuses_a_past_or_same_date(auth_client, app, user):
    task = Task(user_id=user.id, title="Sem volta", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    auth_client.post(
        f"/tasks/{task.id}/migrate",
        data={"target_date": date.today().isoformat()},
        follow_redirects=True,
    )

    assert db.session.get(Task, task.id).migration_count == 0


def test_migrate_blocks_duplicate_twin_on_target_day(auth_client, app, user):
    """Adiar para um dia que já tem tarefa aberta com o mesmo nome não duplica."""
    hoje = date.today()
    amanha = hoje + timedelta(days=1)
    original = Task(user_id=user.id, title="Cuidado Pessoal", scheduled_date=hoje)
    gemea = Task(user_id=user.id, title="Cuidado Pessoal", scheduled_date=amanha)
    db.session.add_all([original, gemea])
    db.session.commit()

    response = auth_client.post(
        f"/tasks/{original.id}/migrate",
        data={"preset": "next_day"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Já existe uma tarefa aberta".encode() in response.data
    assert Task.for_user(user).filter_by(title="Cuidado Pessoal").count() == 2  # nada duplicado
    assert db.session.get(Task, original.id).scheduled_date == hoje  # não se moveu


def test_migrate_allows_twin_that_is_already_done(auth_client, app, user):
    """Uma gêmea concluída no destino não impede o adiamento."""
    hoje = date.today()
    original = Task(user_id=user.id, title="Repetida", scheduled_date=hoje)
    concluida = Task(user_id=user.id, title="Repetida", scheduled_date=hoje + timedelta(days=1), status="done")
    db.session.add_all([original, concluida])
    db.session.commit()

    auth_client.post(f"/tasks/{original.id}/migrate", data={"preset": "next_day"}, follow_redirects=True)

    assert db.session.get(Task, original.id).scheduled_date == hoje + timedelta(days=1)


def test_detail_offers_relative_postpone_options(auth_client, app, user):
    """A tela de detalhe não oferece mais "Migrar p/ amanhã" absoluto."""
    task = Task(user_id=user.id, title="Detalhe", scheduled_date=date.today() + timedelta(days=1))
    db.session.add(task)
    db.session.commit()

    body = auth_client.get(f"/tasks/{task.id}").data.decode("utf-8")
    assert "Migrar p/ amanhã" not in body
    assert "Adiar" in body
    assert 'name="preset" value="next_day"' in body
    # o mínimo do seletor de data é depois da data atual da tarefa
    minimo = (date.today() + timedelta(days=2)).isoformat()
    assert f'min="{minimo}"' in body


def test_migrated_task_is_not_listed_as_done_today(auth_client, app, user):
    """Quem migra para amanhã não pode aparecer em 'Concluídas hoje'."""
    task = Task(user_id=user.id, title="Para amanhã", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    auth_client.post(f"/tasks/{task.id}/migrate", data={"preset": "tomorrow"}, follow_redirects=True)
    body = auth_client.get("/").data.decode("utf-8")

    assert "Nada concluído hoje ainda" in body  # seção de concluídas vazia
    assert "Para amanhã" in body  # segue viva, agora nos próximos dias
    assert "is-done" not in body


def test_bulk_action_box_is_gone(auth_client):
    """A caixa de ações em lote saiu: cada tarefa é única."""
    for path in ("/", "/day", "/monthly", "/tasks/"):
        body = auth_client.get(path).data.decode("utf-8")
        assert 'name="action"' not in body, path
        assert "/tasks/bulk" not in body, path


def test_bulk_route_no_longer_exists(auth_client):
    response = auth_client.post("/tasks/bulk", data={"action": "complete", "ids": ["1"]})
    assert response.status_code == 404
def test_other_users_task_is_not_accessible(auth_client, app, user):
    other = make_user("carla")
    task = Task(user_id=other.id, title="Dela", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    assert auth_client.get(f"/tasks/{task.id}/edit").status_code == 404
    assert auth_client.post(f"/tasks/{task.id}/toggle").status_code == 404
    assert auth_client.post(f"/tasks/{task.id}/migrate").status_code == 404


def test_cli_dedupe_tasks(app, runner, user):
    """Remove duplicatas exatas (mesmo usuário, título, data e situação)."""
    for _ in range(3):
        db.session.add(Task(user_id=user.id, title="Duplicada", scheduled_date=date.today()))
    db.session.add(Task(user_id=user.id, title="Única", scheduled_date=date.today()))
    db.session.add(Task(user_id=user.id, title="Duplicada", scheduled_date=date.today() + timedelta(days=1)))
    db.session.commit()

    # dry-run: mostra o plano e não altera nada
    plan = runner.invoke(args=["dedupe-tasks"])
    assert plan.exit_code == 0, plan.output
    assert "2 duplicata" in plan.output
    assert Task.query.filter_by(title="Duplicada").count() == 4

    applied = runner.invoke(args=["dedupe-tasks", "--apply"])
    assert applied.exit_code == 0, applied.output
    # 3 iguais viram 1; a de outro dia e a única continuam
    assert Task.query.filter_by(title="Duplicada").count() == 2
    assert Task.query.filter_by(title="Única").count() == 1


def test_cli_dedupe_reports_when_clean(app, runner, user, sample_tasks):
    result = runner.invoke(args=["dedupe-tasks"])
    assert result.exit_code == 0
    assert "Nenhuma duplicata" in result.output


def test_migrate_history_is_preserved_in_backup(auth_client, app, user):
    """O histórico de migração vai e volta no backup."""
    from app.backup import service

    task = Task(user_id=user.id, title="Com histórico", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()
    task.migrate_to(date.today() + timedelta(days=2))
    db.session.commit()

    payload = service.export_tasks(user)
    item = next(t for t in payload["tasks"] if t["title"] == "Com histórico")
    assert item["migration_count"] == 1
    assert item["migrated_at"] is not None

    db.session.delete(task)
    db.session.commit()
    service.restore_tasks(user, payload, mode="merge")

    restaurada = Task.for_user(user).filter_by(title="Com histórico").one()
    assert restaurada.migration_count == 1
    assert restaurada.scheduled_date == date.today() + timedelta(days=2)


def test_cli_dedupe_across_days_removes_migration_artifacts(app, runner, user):
    """--across-days limpa o par deixado pela migração-cópia antiga."""
    hoje = date.today()
    amanha = hoje + timedelta(days=1)
    original = Task(user_id=user.id, title="Cuidado Pessoal", scheduled_date=hoje, status="done")
    copia = Task(user_id=user.id, title="Cuidado Pessoal", scheduled_date=amanha, migrated_from_id=None)
    db.session.add_all([original, copia])
    db.session.commit()
    copia.migrated_from_id = original.id
    db.session.commit()

    plano = runner.invoke(args=["dedupe-tasks", "--across-days"])
    assert plano.exit_code == 0, plano.output
    assert "artefato de migração" in plano.output
    assert Task.query.count() == 2

    runner.invoke(args=["dedupe-tasks", "--across-days", "--apply"])
    restantes = Task.for_user(user).all()
    assert [t.id for t in restantes] == [copia.id]  # fica a pendente, mais recente


def test_cli_dedupe_across_days_keeps_legit_repeats(app, runner, user):
    """Sem ligação de migração, tarefas abertas repetidas são preservadas."""
    for dias in (0, 1, 2):
        db.session.add(Task(user_id=user.id, title="Ler 30 páginas", scheduled_date=date.today() + timedelta(days=dias)))
    db.session.commit()

    plano = runner.invoke(args=["dedupe-tasks", "--across-days"])
    assert "Nenhuma duplicata" in plano.output
    assert Task.for_user(user).count() == 3


class TestFiltroConcluidas:
    """O switch "Incluir concluídas" precisa funcionar nos dois sentidos.

    Regressão: o checkbox desmarcado não enviava parâmetro nenhum e o servidor
    caía no padrão "incluir" — desligar o switch não filtrava nada (a lista
    voltava igual e o switch reaparecia ligado).
    """

    @pytest.fixture()
    def com_concluida(self, app, user):
        from datetime import date

        from app.extensions import db
        from app.models import Task

        aberta = Task(user_id=user.id, title="Aberta", scheduled_date=date.today())
        feita = Task(user_id=user.id, title="Feita", scheduled_date=date.today(), status="done")
        db.session.add_all([aberta, feita])
        db.session.commit()
        return aberta, feita

    def test_sem_parametro_mostra_tudo(self, auth_client, com_concluida):
        body = auth_client.get("/tasks/").data.decode("utf-8")
        assert "Aberta" in body
        assert "Feita" in body

    def test_include_done_desligado_esconde_concluidas(self, auth_client, com_concluida):
        body = auth_client.get("/tasks/?include_done=0").data.decode("utf-8")
        assert "Aberta" in body
        assert ">Feita<" not in body

    def test_include_done_ligado_mostra_concluidas(self, auth_client, com_concluida):
        body = auth_client.get("/tasks/?include_done=1").data.decode("utf-8")
        assert "Feita" in body

    def test_oculto_zero_vem_antes_do_checkbox_e_vale_o_ultimo(self, auth_client, com_concluida):
        """O formulário manda 0 (oculto) e, ligado, 1 (checkbox): vale o último."""
        # como o navegador envia quando o switch está ligado
        body = auth_client.get("/tasks/?include_done=0&include_done=1").data.decode("utf-8")
        assert "Feita" in body

    def test_switch_reflete_o_estado_do_filtro(self, auth_client, com_concluida):
        """O switch desenhado bate com o filtro que o servidor aplicou."""

        def switch_ligado(html: str) -> bool:
            return "checked" in html.split('id="f-done"', 1)[1][:120]

        assert switch_ligado(auth_client.get("/tasks/?include_done=1").data.decode("utf-8")) is True
        assert switch_ligado(auth_client.get("/tasks/?include_done=0").data.decode("utf-8")) is False
        # sem parâmetro nenhum o padrão é mostrar tudo (switch ligado)
        assert switch_ligado(auth_client.get("/tasks/").data.decode("utf-8")) is True

    def test_formulario_tem_oculto_e_aplica_na_hora(self, auth_client, com_concluida):
        body = auth_client.get("/tasks/").data.decode("utf-8")
        assert 'data-auto-submit' in body
        assert '<input type="hidden" name="include_done" value="0">' in body
