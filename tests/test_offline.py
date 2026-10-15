"""Modo offline: service worker, fila idempotente e ganchos no HTML.

O que dá para garantir sem navegador:

* o service worker é servido da raiz, com escopo liberado e sem cache HTTP —
  um worker em ``/static/`` só controlaria ``/static/``;
* a lista de precache cobre **todo** o ``/static``: um asset esquecido é o que
  faz o app abrir "pela metade" quando a rede cai;
* a página ``/offline`` abre sem login e sem depender de nenhum asset;
* as rotas de escrita aceitam a chave de idempotência e não duplicam nada —
  é isto que segura o caso clássico de "o servidor gravou, a resposta se
  perdeu, o navegador reenviou";
* a situação da tarefa é aplicada como valor absoluto, então dois cliques não
  desfazem o primeiro;
* os ganchos que o JavaScript procura (classes, data-attributes e a ordem dos
  ``<script>``) estão no HTML renderizado.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.extensions import db
from app.models import ClientOperation, Task
from app.offline import IDEMPOTENCY_FIELD, IDEMPOTENCY_HEADER
from app.pwa import NEVER_CACHE_PREFIXES, _static_files

from .conftest import login, make_user

XHR = {"X-Requested-With": "XMLHttpRequest"}


def _chave(valor: str) -> dict:
    return {IDEMPOTENCY_HEADER: valor}


def _xhr_com_chave(valor: str) -> dict:
    return {**XHR, **_chave(valor)}


# --------------------------------------------------------------------------
# Service worker e página offline
# --------------------------------------------------------------------------


def test_service_worker_servido_da_raiz_com_escopo_liberado(client):
    resposta = client.get("/sw.js")

    assert resposta.status_code == 200
    assert resposta.headers["Content-Type"].startswith("application/javascript")
    # Sem este cabeçalho o worker registrado em /sw.js não pode controlar / e
    # o app ficaria sem offline justamente nas páginas que interessam.
    assert resposta.headers["Service-Worker-Allowed"] == "/"
    assert "no-cache" in resposta.headers["Cache-Control"]


def test_service_worker_nao_esta_no_static(app):
    """Servido como arquivo estático, o escopo ficaria preso em /static/."""
    from pathlib import Path

    assert not (Path(app.static_folder) / "sw.js").exists()
    assert (Path(app.root_path) / "templates" / "sw.js").exists()


def test_service_worker_precacheia_todo_o_static(app, client):
    corpo = client.get("/sw.js").data.decode("utf-8")

    arquivos = _static_files(app.static_folder)
    assert arquivos, "app/static não pode estar vazio"
    for relativo in arquivos:
        assert f'"/static/{relativo}"' in corpo, relativo

    # A tela de "sem conexão" também precisa estar guardada de antemão.
    with app.test_request_context():
        from flask import url_for

        assert f'"{url_for("offline_page")}"' in corpo


def test_service_worker_nao_guarda_rotas_de_sessao(client):
    corpo = client.get("/sw.js").data.decode("utf-8")

    assert "/auth/" in corpo
    assert "/backup/" in corpo
    for prefixo in NEVER_CACHE_PREFIXES:
        assert f'"{prefixo}"' in corpo, prefixo


def test_versao_do_cache_muda_quando_um_asset_muda(app):
    """O navegador só troca o cache se o conteúdo de sw.js mudar."""
    from app.pwa import _fingerprint

    primeira = _fingerprint(app.static_folder, "1.0")
    assert primeira != _fingerprint(app.static_folder, "1.1")
    assert primeira == _fingerprint(app.static_folder, "1.0")  # estável no boot


def test_pagina_offline_abre_sem_login_e_sem_assets(client):
    resposta = client.get("/offline")
    corpo = resposta.data.decode("utf-8")

    assert resposta.status_code == 200
    assert resposta.headers["Content-Type"].startswith("text/html")
    assert "sem conexão" in corpo.lower()
    # Se dependesse de CSS/JS, não serviria para o caso em que nada carregou.
    assert "/static/" not in corpo


def test_paginas_autenticadas_se_identificam_para_o_service_worker(client, auth_client, user):
    anonima = client.get("/auth/login").data.decode("utf-8")
    assert "data-todonow-auth" not in anonima

    autenticada = auth_client.get("/").data.decode("utf-8")
    assert 'data-todonow-auth="1"' in autenticada
    # O service worker usa isto para não guardar página de um usuário e servir
    # para outro no mesmo aparelho.
    assert f'data-todonow-user="{user.username}"' in autenticada


def test_offline_js_carrega_antes_do_todonow(app):
    """O listener de submit do offline.js precisa ser registrado primeiro.

    É ele que decide se um formulário vai para a fila; se o todonow.js (que
    mexe no DOM) viesse antes, a ordem das ações mudaria.
    """
    from pathlib import Path

    base = (Path(app.root_path) / "templates" / "base.html").read_text(encoding="utf-8")

    assert base.index("js/offline.js") < base.index("js/todonow.js")


# --------------------------------------------------------------------------
# Chave de idempotência
# --------------------------------------------------------------------------


def test_quick_add_com_a_mesma_chave_nao_duplica(auth_client, app, user):
    dados = {"title": "Comprar pão", "kind": "task", "priority": "none"}

    primeira = auth_client.post("/tasks/quick", data=dados, headers=_xhr_com_chave("op-1"))
    segunda = auth_client.post("/tasks/quick", data=dados, headers=_xhr_com_chave("op-1"))

    assert primeira.status_code == 201
    assert segunda.status_code == 201
    assert primeira.get_json()["task"]["uuid"] == segunda.get_json()["task"]["uuid"]
    assert Task.query.filter_by(title="Comprar pão").count() == 1


def test_quick_add_com_chaves_diferentes_cria_duas(auth_client, app, user):
    dados = {"title": "Duas vezes de propósito"}

    auth_client.post("/tasks/quick", data=dados, headers=_xhr_com_chave("a"))
    auth_client.post("/tasks/quick", data=dados, headers=_xhr_com_chave("b"))

    assert Task.query.filter_by(title="Duas vezes de propósito").count() == 2


def test_chave_tambem_vale_como_campo_do_formulario(auth_client, app, user):
    """Suporte a reenvio manual (curl, script de diagnóstico)."""
    dados = {"title": "Via campo", IDEMPOTENCY_FIELD: "campo-1"}

    auth_client.post("/tasks/quick", data=dados, headers=XHR)
    auth_client.post("/tasks/quick", data=dados, headers=XHR)

    assert Task.query.filter_by(title="Via campo").count() == 1


def test_chave_vale_por_rota(auth_client, app, user):
    """A mesma chave em rotas diferentes são ações diferentes, não replay."""
    task = Task(user_id=user.id, title="Alvo", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    auth_client.post("/tasks/quick", data={"title": "Nova"}, headers=_xhr_com_chave("mesma"))
    auth_client.post(f"/tasks/{task.id}/status", data={"status": "done"}, headers=_xhr_com_chave("mesma"))

    assert Task.query.filter_by(title="Nova").count() == 1
    assert db.session.get(Task, task.id).status == "done"


def test_chave_e_por_usuario(auth_client, app, user):
    """Aparelho compartilhado: a chave de um não pode cancelar a ação do outro."""
    outro = make_user("bia", "senha12345")

    auth_client.post("/tasks/quick", data={"title": "Da Ana"}, headers=_xhr_com_chave("op-x"))

    # O Flask-Login guarda o usuário no contexto do app, e nos testes o mesmo
    # contexto é reaproveitado por todos os requests: sem sair antes, a segunda
    # requisição ainda seria da Ana (em produção cada request tem seu contexto).
    auth_client.post("/auth/logout")

    cliente = app.test_client()
    login(cliente, username="bia")
    resposta = cliente.post("/tasks/quick", data={"title": "Da Bia"}, headers=_xhr_com_chave("op-x"))

    assert resposta.status_code == 201
    assert resposta.get_json()["task"]["title"] == "Da Bia"
    assert Task.query.filter_by(user_id=user.id, title="Da Ana").count() == 1
    assert Task.query.filter_by(user_id=outro.id, title="Da Bia").count() == 1


def test_replay_devolve_o_mesmo_status_e_corpo(auth_client, app, user):
    auth_client.post("/tasks/quick", data={"title": "Repetida"}, headers=_xhr_com_chave("op-2"))
    resposta = auth_client.post("/tasks/quick", data={"title": "Repetida"}, headers=_xhr_com_chave("op-2"))

    assert resposta.status_code == 201
    assert resposta.get_json()["ok"] is True
    assert resposta.get_json()["task"]["toggle_url"].endswith("/toggle")


def test_requisicao_sem_chave_continua_normal(auth_client, app, user):
    """Quem não é o cliente offline não ganha comportamento novo."""
    auth_client.post("/tasks/quick", data={"title": "Sem chave"}, headers=XHR)
    auth_client.post("/tasks/quick", data={"title": "Sem chave"}, headers=XHR)

    assert Task.query.filter_by(title="Sem chave").count() == 2


def test_prune_apaga_chaves_antigas(app, user):
    from app.offline import prune
    from app.utils import now_local

    antiga = ClientOperation(
        user_id=user.id,
        key="velha",
        endpoint="tasks.quick_add",
        status_code=201,
        created_at=now_local() - timedelta(days=30),
        updated_at=now_local() - timedelta(days=30),
    )
    nova = ClientOperation(user_id=user.id, key="nova", endpoint="tasks.quick_add", status_code=201)
    db.session.add_all([antiga, nova])
    db.session.commit()

    assert prune(7) == 1
    db.session.commit()
    restantes = [registro.key for registro in ClientOperation.query.all()]
    assert restantes == ["nova"]


# --------------------------------------------------------------------------
# Criar/editar por AJAX (o caminho que a fila offline reusa)
# --------------------------------------------------------------------------


def test_create_via_ajax_devolve_json_com_redirect(auth_client, app, user):
    resposta = auth_client.post(
        "/tasks/new",
        data={"title": "Criada offline", "kind": "task", "priority": "none", "status": "open",
              "scheduled_date": date.today().isoformat()},
        headers=_xhr_com_chave("create-1"),
    )

    assert resposta.status_code == 201
    payload = resposta.get_json()
    assert payload["ok"] is True
    assert payload["task"]["title"] == "Criada offline"
    assert payload["redirect"].startswith("/")

    repetida = auth_client.post(
        "/tasks/new",
        data={"title": "Criada offline", "kind": "task", "priority": "none", "status": "open",
              "scheduled_date": date.today().isoformat()},
        headers=_xhr_com_chave("create-1"),
    )
    assert repetida.status_code == 201
    assert Task.query.filter_by(title="Criada offline").count() == 1


def test_create_via_ajax_com_erro_de_validacao_devolve_400_json(auth_client):
    resposta = auth_client.post("/tasks/new", data={"title": ""}, headers=XHR)

    assert resposta.status_code == 400
    payload = resposta.get_json()
    assert payload["ok"] is False
    assert "title" in payload["errors"]


def test_edit_via_ajax_devolve_json(auth_client, app, user):
    task = Task(user_id=user.id, title="Antes", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    resposta = auth_client.post(
        f"/tasks/{task.id}/edit",
        data={"title": "Depois", "kind": "task", "priority": "high", "status": "open",
              "scheduled_date": date.today().isoformat()},
        headers=XHR,
    )

    assert resposta.status_code == 200
    assert resposta.get_json()["task"]["title"] == "Depois"


# --------------------------------------------------------------------------
# Situação absoluta (o duplo clique do usuário instável)
# --------------------------------------------------------------------------


def test_set_status_e_absoluto_e_sobrevive_a_dois_cliques(auth_client, app, user):
    task = Task(user_id=user.id, title="Clique duplo", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    primeira = auth_client.post(f"/tasks/{task.id}/status", data={"status": "done"}, headers=XHR)
    segunda = auth_client.post(f"/tasks/{task.id}/status", data={"status": "done"}, headers=XHR)

    assert primeira.get_json()["changed"] is True
    assert segunda.get_json()["changed"] is False
    assert db.session.get(Task, task.id).status == "done"
    assert db.session.get(Task, task.id).completed_at is not None


def test_set_status_reabre(auth_client, app, user):
    task = Task(user_id=user.id, title="Reabrir", scheduled_date=date.today())
    task.complete()
    db.session.add(task)
    db.session.commit()

    auth_client.post(f"/tasks/{task.id}/status", data={"status": "open"}, headers=XHR)

    atual = db.session.get(Task, task.id)
    assert atual.status == "open"
    assert atual.completed_at is None


def test_set_status_recusa_valor_invalido(auth_client, app, user):
    task = Task(user_id=user.id, title="Inválida", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    resposta = auth_client.post(f"/tasks/{task.id}/status", data={"status": "migrated"}, headers=XHR)

    assert resposta.status_code == 400
    assert resposta.get_json()["ok"] is False
    assert db.session.get(Task, task.id).status == "open"


def test_set_status_de_outro_usuario_da_404(auth_client, app, admin):
    task = Task(user_id=admin.id, title="Do admin", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    assert auth_client.post(f"/tasks/{task.id}/status", data={"status": "done"}).status_code == 404


def test_set_status_sem_javascript_redireciona(auth_client, app, user):
    task = Task(user_id=user.id, title="Sem JS", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    resposta = auth_client.post(f"/tasks/{task.id}/status", data={"status": "done"})

    assert resposta.status_code == 302
    assert db.session.get(Task, task.id).status == "done"


# --------------------------------------------------------------------------
# Remover e adiar reenviados
# --------------------------------------------------------------------------


def test_delete_reenviado_nao_falha(auth_client, app, user):
    task = Task(user_id=user.id, title="Remover", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    primeira = auth_client.post(f"/tasks/{task.id}/delete", headers=_xhr_com_chave("del-1"))
    segunda = auth_client.post(f"/tasks/{task.id}/delete", headers=_xhr_com_chave("del-1"))

    assert primeira.status_code == 200
    # Sem a chave isto seria 404: a tarefa já não existe.
    assert segunda.status_code == 200
    assert segunda.get_json()["deleted"] == task.id
    assert db.session.get(Task, task.id) is None


def test_migrate_com_data_absoluta_nao_conta_duas_vezes(auth_client, app, user):
    task = Task(user_id=user.id, title="Adiar", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    destino = (date.today() + timedelta(days=1)).isoformat()
    dados = {"target_date": destino}
    auth_client.post(f"/tasks/{task.id}/migrate", data=dados, headers=_xhr_com_chave("mig-1"))
    auth_client.post(f"/tasks/{task.id}/migrate", data=dados, headers=_xhr_com_chave("mig-1"))

    atual = db.session.get(Task, task.id)
    assert atual.scheduled_date == date.today() + timedelta(days=1)
    assert atual.migration_count == 1


def test_anotacoes_com_chave_nao_duplicam(auth_client, app, user):
    task = Task(user_id=user.id, title="Notas", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    auth_client.post(f"/tasks/{task.id}/notes", data={"notes": "linha"}, headers=_xhr_com_chave("nota-1"))
    auth_client.post(f"/tasks/{task.id}/notes", data={"notes": "linha"}, headers=_xhr_com_chave("nota-1"))

    assert ClientOperation.query.filter_by(key="nota-1").count() == 1


# --------------------------------------------------------------------------
# Ganchos no HTML que o JavaScript procura
# --------------------------------------------------------------------------


def test_linha_da_tarefa_tem_ganchos_de_offline(auth_client, app, user):
    task = Task(user_id=user.id, title="Ganchos", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    corpo = auth_client.get("/tasks/").data.decode("utf-8")

    assert f'action="/tasks/{task.id}/toggle"' in corpo  # o caminho sem JS continua
    assert f'data-status-url="/tasks/{task.id}/status"' in corpo
    assert 'data-status-next="done"' in corpo
    assert 'data-offline="ajax"' in corpo
    assert "js-toggle-task" in corpo
    assert "js-migrate" in corpo
    assert "js-delete-task" in corpo
    assert 'data-task-kind="task"' in corpo
    assert 'name="status" value="done"' in corpo


def test_formularios_longos_salvam_rascunho(auth_client, app, user):
    task = Task(user_id=user.id, title="Rascunho", scheduled_date=date.today())
    db.session.add(task)
    db.session.commit()

    assert 'data-draft="auto"' in auth_client.get("/tasks/new").data.decode("utf-8")
    assert 'data-draft="auto"' in auth_client.get(f"/tasks/{task.id}/edit").data.decode("utf-8")
    assert 'data-draft="auto"' in auth_client.get(f"/tasks/{task.id}").data.decode("utf-8")


def test_formulario_rapido_e_ajax_e_guarda_rascunho(auth_client):
    corpo = auth_client.get("/").data.decode("utf-8")

    assert "js-quick-add" in corpo
    assert 'data-draft="auto"' in corpo
    assert 'data-offline="ajax"' in corpo


def test_a_decisao_de_enfileirar_vem_do_nucleo_conferido(auth_client, client):
    """Sair, entrar e restaurar backup dependem do servidor: não vão para a fila
    (não haveria como aplicar depois, e a sessão seria outra).

    A regra mora em offline-core.js, que o harness em Node exercita de verdade
    (tests/js/offline_harness.mjs); aqui só garantimos que o offline.js não
    reimplementou a decisão por conta própria.
    """
    offline_js = auth_client.get("/static/js/offline.js").data.decode("utf-8")
    core_js = client.get("/static/js/offline-core.js").data.decode("utf-8")

    assert "core.deveEnfileirar(" in offline_js
    assert "caminho.indexOf('/auth/') === 0" in core_js
    assert "caminho.indexOf('/backup/') === 0" in core_js

