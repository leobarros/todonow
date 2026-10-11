"""Anotações em Markdown: renderização, sanitização e rotas de edição."""

from __future__ import annotations

from datetime import date

from app import markdown_notes
from app.extensions import db
from app.models import Task

from .conftest import make_user

# --------------------------------------------------------------------------
# Renderização
# --------------------------------------------------------------------------


def test_markdown_basico():
    html = str(markdown_notes.render("**negrito** e *itálico* e `código`"))
    assert "<strong>negrito</strong>" in html
    assert "<em>itálico</em>" in html
    assert "<code>código</code>" in html


def test_markdown_listas_e_tabelas():
    html = str(markdown_notes.render("- um\n- dois"))
    assert "<ul>" in html and "<li>um</li>" in html

    tabela = str(markdown_notes.render("| a | b |\n|---|---|\n| 1 | 2 |"))
    assert "<table>" in tabela and "<td>1</td>" in tabela


def test_itens_bujo_viram_simbolo_e_riscado():
    """Estilo BUJO: símbolo + riscado, sem checkbox de formulário."""
    html = str(markdown_notes.render("- [ ] revisar backup\n- [x] subir container"))
    assert "checkbox" not in html and "<input" not in html
    assert 'data-bujo=" "' in html and 'data-bujo="x"' in html
    assert '<span class="bujo-symbol">•</span>' in html
    assert '<span class="bujo-symbol">✕</span>' in html
    # concluído vem riscado; pendente não
    assert "bujo-done is-struck" in html
    assert "bujo-open" in html
    # o símbolo vem antes do texto e o texto fica em .bujo-text
    assert html.index("bujo-symbol") < html.index("revisar backup")
    assert 'class="bujo-text"' in html


def test_todos_os_marcadores_do_usuario():
    """Os marcadores do dia a dia: [ ] [x] [-] [>] [<] [o]."""
    html = str(
        markdown_notes.render(
            "- [ ] pendente\n- [x] concluída\n- [-] irrelevante\n"
            "- [>] migrada\n- [<] agendada\n- [o] evento"
        )
    )
    for atributo, simbolo in (
        (" ", "•"),
        ("x", "✕"),
        ("-", "~"),
        ("&gt;", "&gt;"),
        ("&lt;", "&lt;"),
        ("o", "○"),
    ):
        assert f'data-bujo="{atributo}"' in html, atributo
        assert f'<span class="bujo-symbol">{simbolo}</span>' in html, atributo
    # riscados: concluída e irrelevante
    assert html.count("is-struck") == 2
    # migrada/agendada/evento têm classe própria (cor de tom)
    assert "bujo-migrated" in html and "bujo-scheduled" in html and "bujo-event" in html


def test_marcador_maiusculo_funciona():
    html = str(markdown_notes.render("- [X] feito\n- [O] reunião"))
    assert 'data-bujo="x"' in html and 'data-bujo="o"' in html


def test_markdown_escapa_html_perigoso():
    """Nada de script/onerror/javascript: entra na allowlist e sai limpo."""
    sujo = (
        '<script>alert("xss")</script>'
        '<img src=x onerror="alert(1)">'
        '<a href="javascript:alert(1)">clique</a>'
        '<iframe src="https://exemplo.com"></iframe>'
        "texto normal"
    )
    html = str(markdown_notes.render(sujo))
    assert "<script" not in html
    assert "onerror" not in html
    assert "javascript:" not in html
    assert "<iframe" not in html
    assert "texto normal" in html


def test_markdown_links_seguros_recebem_rel():
    html = str(markdown_notes.render("[site](https://exemplo.com)"))
    assert 'href="https://exemplo.com"' in html
    assert "noopener" in html


def test_markdown_vazio():
    assert str(markdown_notes.render(None)) == ""
    assert str(markdown_notes.render("   \n  ")) == ""
    assert markdown_notes.tem_conteudo("  ") is False
    assert markdown_notes.tem_conteudo("- [ ] algo") is True


def test_preview_em_texto_puro():
    texto = "# Reunião\n\n- [ ] revisar backup\n- [x] subir container\n\n**fim**"
    resumo = markdown_notes.para_texto(texto, 200)
    assert "Reunião" in resumo
    assert "revisar backup" in resumo
    assert "•" in resumo and "✕" in resumo  # o símbolo aparece no resumo
    assert "<" not in resumo and "**" not in resumo and "#" not in resumo

    curto = markdown_notes.para_texto("a" * 300, 50)
    assert len(curto) <= 50 and curto.endswith("…")


def test_render_cacheado_devolve_mesmo_resultado():
    a = markdown_notes.render_cached("- [ ] item")
    b = markdown_notes.render_cached("- [ ] item")
    assert a == b


# --------------------------------------------------------------------------
# Rotas
# --------------------------------------------------------------------------


def _task(user, **kwargs):
    kwargs.setdefault("title", "Com anotações")
    kwargs.setdefault("scheduled_date", date.today())
    task = Task(user_id=user.id, **kwargs)
    db.session.add(task)
    db.session.commit()
    return task


def test_salvar_anotacoes(auth_client, app, user):
    task = _task(user)

    resposta = auth_client.post(
        f"/tasks/{task.id}/notes",
        data={"notes": "- [ ] revisar\n- [x] feito"},
        follow_redirects=True,
    )

    assert resposta.status_code == 200
    assert b"Anota" in resposta.data
    assert db.session.get(Task, task.id).notes == "- [ ] revisar\n- [x] feito"
    # e o detalhe já mostra o Markdown renderizado no estilo BUJO
    assert b"bujo-symbol" in resposta.data
    assert b"is-struck" in resposta.data  # o item [x] aparece riscado


def test_limpar_anotacoes(auth_client, app, user):
    task = _task(user, notes="texto antigo")

    auth_client.post(
        f"/tasks/{task.id}/notes",
        data={"notes": "texto antigo", "clear_notes": "1"},
        follow_redirects=True,
    )

    assert db.session.get(Task, task.id).notes is None


def test_anotacoes_longas_sao_recusadas(auth_client, app, user):
    task = _task(user, notes="curto")

    resposta = auth_client.post(
        f"/tasks/{task.id}/notes",
        data={"notes": "x" * 20001},
        follow_redirects=True,
    )

    assert b"passam de" in resposta.data
    assert db.session.get(Task, task.id).notes == "curto"  # nada foi salvo


def test_anotacoes_sao_sanitizadas_ao_renderizar(auth_client, app, user):
    task = _task(user, notes='<script>alert("x")</script>\n\n**ok**')

    body = auth_client.get(f"/tasks/{task.id}").data.decode("utf-8")

    assert 'alert("x")' not in body  # o script injetado não passou
    assert "<strong>ok</strong>" in body


def test_anotacoes_de_outro_usuario_nao_podem_ser_editadas(auth_client, app, user):
    outra = make_user("bia")
    task = _task(outra)

    resposta = auth_client.post(f"/tasks/{task.id}/notes", data={"notes": "invasão"})

    assert resposta.status_code == 404
    assert db.session.get(Task, task.id).notes is None


def test_render_devolve_html_do_markdown(auth_client):
    """O servidor renderiza o Markdown — é o que o modo Visual carrega."""
    resposta = auth_client.post(
        "/tasks/notes/preview",
        json={"notes": "- [x] item\n\n**forte**"},
        headers={"Accept": "application/json"},
    )

    assert resposta.status_code == 200
    payload = resposta.get_json()
    assert payload["ok"] is True
    assert 'data-bujo="x"' in payload["html"]
    assert "is-struck" in payload["html"]
    assert "<strong>forte</strong>" in payload["html"]


def test_preview_exige_login(client):
    resposta = client.post("/tasks/notes/preview", json={"notes": "x"})
    assert resposta.status_code in (302, 401)


def test_preview_sanitiza(auth_client):
    resposta = auth_client.post(
        "/tasks/notes/preview",
        json={"notes": "<script>alert(1)</script>"},
        headers={"Accept": "application/json"},
    )
    assert "<script" not in resposta.get_json()["html"]


# --------------------------------------------------------------------------
# Onde a feature aparece na interface
# --------------------------------------------------------------------------


def test_editor_tem_modo_visual_e_markdown(auth_client, app, user):
    task = _task(user)

    for pagina in ("/tasks/new", f"/tasks/{task.id}"):
        body = auth_client.get(pagina).data.decode("utf-8")
        assert "data-md-editor" in body, pagina
        assert 'data-md-mode="visual"' in body, pagina
        assert 'data-md-mode="markdown"' in body, pagina
        assert "data-md-area" in body, pagina  # área WYSIWYG (atributo próprio)
        assert "data-md-placeholder" in body or "data-placeholder" in body, pagina
        assert "js/notes.js" in body, pagina
        assert "vendor/turndown.js" in body, pagina  # HTML -> Markdown
        # os seis marcadores BUJO estão na barra
        for marca in ("bujo: ", "bujo:x", "bujo:-", "bujo:>", "bujo:<", "bujo:o"):
            assert f'data-md-visual="{marca}"' in body, (pagina, marca)

    detalhe = auth_client.get(f"/tasks/{task.id}").data.decode("utf-8")
    assert f'action="/tasks/{task.id}/notes"' in detalhe


def test_detalhe_mostra_anotacoes_renderizadas(auth_client, app, user):
    task = _task(user, notes="# Histórico\n\n- item um\n- item dois")

    body = auth_client.get(f"/tasks/{task.id}").data.decode("utf-8")

    assert "<h1>Histórico</h1>" in body
    assert "<li>item um</li>" in body
    assert "Salvar anotações" in body  # editor disponível para editar


def test_linha_mostra_indicador_e_resumo_renderizado(auth_client, app, user):
    com = _task(user, notes="- [x] Fazer testes\n- [ ] Diferenciar os cofres")
    sem = _task(user, title="Sem anotações")

    body = auth_client.get("/tasks/").data.decode("utf-8")
    trecho_com = body[body.index(f'action="/tasks/{com.id}/toggle"') :]
    assert "note-badge" in trecho_com
    # o resumo na linha vem renderizado, com símbolo e riscado — e não o "- [x]" cru
    assert "bujo-done is-struck" in trecho_com
    assert "Fazer testes" in trecho_com
    assert "- [x] Fazer testes" not in trecho_com

    trecho_sem = body[body.index(f'action="/tasks/{sem.id}/toggle"') :]
    assert "note-badge" not in trecho_sem[:600]


def test_editor_de_anotacoes_fica_fora_da_regiao_viva(auth_client, app, user):
    """Concluir a tarefa recarrega o cartão, mas não pode apagar texto não salvo."""
    from html.parser import HTMLParser

    class Ancestrais(HTMLParser):
        VAZIAS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
                  "link", "meta", "param", "source", "track", "wbr"}

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.pilha = []
            self.dentro_da_regiao = None

        def handle_starttag(self, tag, attrs):
            atributos = dict(attrs)
            if atributos.get("id") == "anotacoes":
                self.dentro_da_regiao = any(
                    a.get("data-live-region") == "tarefa-detalhe" for a in self.pilha
                )
            if tag not in self.VAZIAS:
                self.pilha.append(atributos)

        def handle_endtag(self, tag):
            if tag not in self.VAZIAS and self.pilha:
                self.pilha.pop()

    task = _task(user)
    body = auth_client.get(f"/tasks/{task.id}").data.decode("utf-8")

    parser = Ancestrais()
    parser.feed(body)

    assert parser.dentro_da_regiao is False, "o editor de anotações não pode ser recarregado junto"
    assert 'data-live-region="tarefa-detalhe"' in body  # ...mas a região continua existindo


def test_backup_leva_as_anotacoes(auth_client, app, user):
    from app.backup import service

    task = _task(user, notes="- [ ] pendente")
    payload = service.export_tasks(user)
    item = next(t for t in payload["tasks"] if t["uuid"] == task.uuid)
    assert item["notes"] == "- [ ] pendente"

    db.session.delete(task)
    db.session.commit()
    service.restore_tasks(user, payload, mode="merge")
    assert Task.for_user(user).filter_by(title="Com anotações").one().notes == "- [ ] pendente"
