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


def _quick_add_form(html: str) -> str:
    import re

    match = re.search(r'<form method="post" action="/tasks/quick".*?</form>', html, re.S)
    assert match, "formulário rápido não encontrado na página"
    return match.group(0)


def test_calendar_quick_add_stacks_in_narrow_sidebar(auth_client):
    """Regressão: na coluna de 3 do calendário o form não pode usar só col-lg-*.

    Com as classes horizontais (col-lg-5/col-lg-2/col-lg-1) os campos ficavam
    espremidos dentro do card de ~285px e estouravam a borda.
    """
    form = _quick_add_form(auth_client.get("/calendar").data.decode("utf-8"))
    assert "quick-add-sidebar" in form
    # Empilhado a partir de xl (coluna estreita)...
    assert "col-12 col-lg-5 col-xl-12" in form
    assert "col-6 col-lg-2 col-xl-6" in form
    assert "col-6 col-lg-1 col-xl-12" in form
    # ...e rótulo completo do botão apenas nesse caso.
    assert "d-none d-xl-inline" in form


def test_dashboard_quick_add_keeps_horizontal_layout(auth_client):
    """Fora da barra lateral o layout de uma linha continua valendo."""
    form = _quick_add_form(auth_client.get("/").data.decode("utf-8"))
    assert "quick-add-sidebar" not in form
    assert "col-12 col-lg-5" in form
    assert "col-xl-12" not in form


# --------------------------------------------------------------------------
# Regressões de UX: formulário rápido, regiões vivas e fim das coleções
# --------------------------------------------------------------------------


def test_quick_add_card_has_consistent_padding(auth_client):
    """O card do formulário rápido precisa do mesmo respiro dos outros cards."""
    form = _quick_add_form(auth_client.get("/").data.decode("utf-8"))
    assert "bujo-card p-3 p-md-4" in form


def test_quick_add_has_no_collection_field(auth_client):
    for path in ("/", "/day", "/future", "/calendar"):
        body = auth_client.get(path).data.decode("utf-8")
        assert 'id="qa-collection"' not in body, path
        assert "Coleção" not in body, path


def test_task_form_and_list_have_no_collection(auth_client):
    assert "Coleção" not in auth_client.get("/tasks/new").data.decode("utf-8")
    assert "Coleção" not in auth_client.get("/tasks/").data.decode("utf-8")
    assert 'name="collection"' not in auth_client.get("/tasks/").data.decode("utf-8")


def test_profile_has_no_collections_card(auth_client):
    body = auth_client.get("/auth/profile").data.decode("utf-8")
    assert "Minhas coleções" not in body
    assert "Como usar o BUJO aqui" in body


def test_dashboard_live_regions_are_present(auth_client):
    """As seções que mudam ao concluir uma tarefa ficam em regiões vivas."""
    body = auth_client.get("/").data.decode("utf-8")
    assert 'data-live-region="hoje-progresso"' in body
    assert 'data-live-region="hoje-corpo"' in body


def test_quick_add_stays_outside_live_regions(auth_client):
    """O formulário rápido não pode ser substituído por uma atualização."""
    body = auth_client.get("/").data.decode("utf-8")
    assert body.index('class="quick-add') < body.index('data-live-region="hoje-corpo"')


def test_dashboard_renders_done_section_even_when_empty(auth_client):
    """A seção 'Concluídas hoje' existe sempre, senão não haveria onde a
    primeira tarefa concluída aparecer sem recarregar a página."""
    body = auth_client.get("/").data.decode("utf-8")
    assert "Concluídas hoje" in body
    assert "Nada concluído hoje ainda" in body


def test_live_regions_on_other_pages(auth_client, app, user):
    from datetime import date

    from app.extensions import db
    from app.models import Task

    task = Task(user_id=user.id, title="Região viva", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    assert 'data-live-region="dia-conteudo"' in auth_client.get("/day").data.decode("utf-8")
    list_body = auth_client.get("/tasks/").data.decode("utf-8")
    assert 'data-live-region="tarefas-resumo"' in list_body
    assert 'data-live-region="tarefas-lista"' in list_body
    assert 'data-live-region="tarefa-detalhe"' in auth_client.get(f"/tasks/{task.id}").data.decode("utf-8")


def test_js_refreshes_live_regions(auth_client):
    """O JS precisa delegar o toggle e saber recarregar as regiões."""
    js = auth_client.get("/static/js/todonow.js").data.decode("utf-8")
    assert "data-live-region" in js
    assert "refreshLiveRegions" in js
    assert "DOMParser" in js
    # delegação: nenhum listener preso a um form específico
    assert "form.classList.contains('js-toggle-task')" in js


# --------------------------------------------------------------------------
# Confirmação em modal (não no window.confirm nativo, que quebra o tema)
# --------------------------------------------------------------------------


def test_confirm_modal_exists_on_authenticated_pages(auth_client):
    for path in ("/", "/tasks/", "/backup/"):
        body = auth_client.get(path).data.decode("utf-8")
        assert 'id="confirm-modal"' in body, path
        assert 'id="confirm-modal-ok"' in body, path
        assert 'id="confirm-modal-note"' in body, path


def test_confirm_modal_uses_warning_tone(auth_client):
    body = auth_client.get("/").data.decode("utf-8")
    modal = body[body.index('id="confirm-modal"') : body.index("</div>\n\n<script")]
    assert "bi-exclamation-triangle-fill text-warning" in modal
    assert "btn-warning" in modal
    assert "Cancelar" in modal  # saída sempre disponível


def test_delete_task_form_has_clear_confirmation(auth_client, app, user):
    from datetime import date

    from app.extensions import db
    from app.models import Task

    task = Task(user_id=user.id, title="Apagar isto", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    body = auth_client.get("/").data.decode("utf-8")
    trecho = body[body.index(f'action="/tasks/{task.id}/delete"') :]
    trecho = trecho[: trecho.index("</form>")]
    assert 'data-confirm="Remover a tarefa &quot;Apagar isto&quot;?"' in trecho
    assert 'data-confirm-label="Sim, remover"' in trecho
    assert "apagada permanentemente" in trecho


def test_js_uses_modal_instead_of_native_confirm(auth_client):
    js = auth_client.get("/static/js/todonow.js").data.decode("utf-8")
    assert "confirm-modal" in js
    assert "new window.bootstrap.Modal" in js
    # o nativo só aparece como último recurso, sem o bundle
    assert js.count("window.confirm(") == 1


# --------------------------------------------------------------------------
# Alvo de conclusão: precisa parecer clicável
# --------------------------------------------------------------------------


def test_check_button_is_a_real_toggle_target(auth_client, app, user):
    from datetime import date

    from app.extensions import db
    from app.models import Task

    task = Task(user_id=user.id, title="Concluir isto", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    body = auth_client.get("/").data.decode("utf-8")
    trecho = body[body.index('class="bujo-check"'):]
    trecho = trecho[: trecho.index("</button>")]
    assert 'aria-pressed="false"' in trecho
    assert "Concluir a tarefa" in trecho
    assert "Clique para concluir" in trecho


def test_completed_task_offers_reopen(auth_client, app, user):
    from datetime import date

    from app.extensions import db
    from app.models import Task

    task = Task(user_id=user.id, title="Já feita", scheduled_date=date.today())
    task.complete()
    db.session.add(task)
    db.session.commit()

    body = auth_client.get("/").data.decode("utf-8")
    trecho = body[body.index('class="bujo-check"'):]
    trecho = trecho[: trecho.index("</button>")]
    assert 'aria-pressed="true"' in trecho
    assert "Reabrir a tarefa" in trecho
    assert "Clique para reabrir" in trecho


def test_sections_teach_the_action(auth_client):
    """As seções lembram o usuário do que fazer, sem texto solto demais."""
    body = auth_client.get("/").data.decode("utf-8")
    assert "clique no círculo para concluir" in body
    assert "Clique no círculo •" in body  # estado vazio de Concluídas hoje

    dia = auth_client.get("/day").data.decode("utf-8")
    assert "clique no círculo para concluir" in dia


def test_task_list_teaches_the_action(auth_client, sample_tasks):
    """Na lista a dica aparece junto das tarefas (só faz sentido com linhas)."""
    lista = auth_client.get("/tasks/").data.decode("utf-8")
    assert "Clique no círculo à esquerda" in lista


def test_ring_style_is_defined(auth_client):
    """O alvo tem anel, hover e foco visível (o que dá a affordance)."""
    css = auth_client.get("/static/css/todonow.css").data.decode("utf-8")
    assert "--bujo-target" in css
    assert ".bujo-check:hover" in css
    assert ".bujo-check:focus-visible" in css
    assert ".task-row:hover .bujo-check" in css
    assert "border-radius: 50%" in css


def test_monthly_log_action_buttons_are_aligned(auth_client):
    """Os botões do card do Mensal ficam em grade — antes quebravam em linhas
    de tamanhos diferentes dentro da coluna estreita."""
    body = auth_client.get("/monthly").data.decode("utf-8")
    assert "bujo-card p-3 h-100 d-grid gap-2 align-content-center" in body
    assert "d-flex flex-wrap gap-2 align-items-center" not in body
    # os três atalhos continuam lá
    for rotulo in ("Calendário", "Future Log", "Nova no mês"):
        assert rotulo in body


def test_selos_usam_classes_validas_do_bootstrap(auth_client, app, user):
    """`text-bg-<cor>-subtle` não existe no Bootstrap 5.3: o selo saía branco
    sobre fundo transparente (invisível). O par correto é bg + text-emphasis."""
    from datetime import date, timedelta

    from app.extensions import db
    from app.models import Task

    futura = Task(user_id=user.id, title="Futura", scheduled_date=date.today() + timedelta(days=3))
    migrada = Task(user_id=user.id, title="Rolada", scheduled_date=date.today(), migration_count=2)
    db.session.add_all([futura, migrada])
    db.session.commit()

    body = auth_client.get("/tasks/").data.decode("utf-8")
    assert "text-bg-info-subtle" not in body
    assert "text-bg-warning-subtle" not in body
    assert "bg-info-subtle text-info-emphasis" in body
    assert "bg-warning-subtle text-warning-emphasis" in body


def test_linha_da_tarefa_nao_move_no_hover(auth_client):
    """Regressão: `transform` no hover da linha criava contexto de empilhamento
    (o menu de adiar ficava atrás do card seguinte) e fazia o hover oscilar —
    o menu tremia/piscava enquanto o mouse passava por ele."""
    css = auth_client.get("/static/css/todonow.css").data.decode("utf-8")

    inicio = css.index(".task-row:hover")
    trecho = css[inicio : inicio + 400]
    assert "transform" not in trecho.split("}")[0], "o hover da linha não pode mover a linha"

    # a linha sobe acima das vizinhas enquanto o menu está aberto
    assert ".task-row:has(.dropdown-menu.show)" in css
    assert ".task-row:focus-within" in css
    assert "z-index: 5" in css

    # o menu do Bootstrap continua acima do conteúdo normal
    assert "position: relative" in css


def test_menu_de_adiar_esta_na_linha_e_nao_em_portal(auth_client, app, user):
    """O menu fica dentro da linha (é o z-index da linha que resolve a sobreposição),
    então o CSS precisa garantir o empilhamento — não basta o z-index do Bootstrap."""
    from datetime import date

    from app.extensions import db
    from app.models import Task

    db.session.add(Task(user_id=user.id, title="Com menu", scheduled_date=date.today()))
    db.session.commit()

    body = auth_client.get("/tasks/").data.decode("utf-8")
    assert 'class="dropdown"' in body
    assert "dropdown-menu dropdown-menu-end" in body
    assert "data-bs-toggle=\"dropdown\"" in body
