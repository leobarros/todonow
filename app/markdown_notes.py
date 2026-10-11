"""Renderização das anotações em Markdown.

As anotações são guardadas como texto puro (Markdown) na coluna ``notes`` — sem
HTML no banco. Na hora de exibir, o texto é convertido para HTML **e sanitizado**
(allowlist do nh3), então nada que o usuário cole de fora consegue injetar script
na página.

As anotações de atividade seguem o estilo BUJO do app — símbolo + riscado, sem
checkbox de formulário::

    - [ ] pendente      ->  •  pendente
    - [x] concluída     ->  ✕  pendente riscada
    - [-] irrelevante   ->  ~  riscada e apagada
    - [>] migrada       ->  >  (rolou para outro dia)
    - [<] agendada      ->  <  (marcada para o futuro)
    - [o] evento        ->  ○
"""

from __future__ import annotations

import re
from functools import lru_cache
from xml.etree import ElementTree as ET

import markdown as _markdown
import nh3
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor
from markupsafe import Markup

# Extensões: blocos de código, tabelas, listas previsíveis e quebra de linha
# simples (ninguém quer escrever dois espaços no fim da linha para pular linha).
MARKDOWN_EXTENSIONS = ["fenced_code", "tables", "sane_lists", "nl2br"]

# Allowlist do HTML final.
ALLOWED_TAGS = {
    "p",
    "br",
    "hr",
    "strong",
    "em",
    "del",
    "code",
    "pre",
    "blockquote",
    "ul",
    "ol",
    "li",
    "a",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "span",
}

ALLOWED_ATTRIBUTES = {
    # "rel" fica de fora: o nh3 o define sozinho via link_rel.
    "a": {"href", "title"},
    "span": {"class"},
    # data-bujo guarda o marcador original ([ ], [x], [>]…) para o editor visual
    # conseguir converter o HTML de volta em Markdown.
    "li": {"class", "data-bujo"},
    "code": {"class"},
    "th": {"align"},
    "td": {"align"},
}

ALLOWED_SCHEMES = {"http", "https", "mailto"}

_MARCADOR_ATIVIDADE = re.compile(r"^\[([ xX\->oO<])\]\s*")

# marcador -> (símbolo BUJO, classe, riscado?)
_ITENS_BUJO = {
    " ": ("•", "bujo-open", False),
    "x": ("✕", "bujo-done", True),
    "-": ("~", "bujo-irrelevant", True),
    ">": (">", "bujo-migrated", False),
    "<": ("<", "bujo-scheduled", False),
    "o": ("○", "bujo-event", False),
}


class _ItemDeAtividade(Treeprocessor):
    """``- [x] texto`` vira um item BUJO: símbolo + riscado, sem checkbox.

    O marcador original fica em ``data-bujo`` para o editor visual poder voltar
    a ser Markdown sem perder a informação.
    """

    def run(self, root):
        for item in root.iter("li"):
            texto = item.text or ""
            achado = _MARCADOR_ATIVIDADE.match(texto)
            if not achado:
                continue

            bruto = achado.group(1)
            marca = bruto.lower() if bruto in "xXoO" else bruto
            simbolo, classe, riscado = _ITENS_BUJO[marca]

            # O conteúdo (texto + formatação inline) vai para dentro de .bujo-text.
            caixa_texto = ET.SubElement(item, "span", {"class": "bujo-text"})
            caixa_texto.text = texto[achado.end() :] or None
            for filho in list(item):
                if filho is caixa_texto:
                    continue
                item.remove(filho)
                caixa_texto.append(filho)

            item.text = None
            item.set("data-bujo", marca)
            item.set("class", " ".join(n for n in ("bujo-item", classe, "is-struck" if riscado else "") if n))

            caixa_simbolo = ET.Element("span", {"class": "bujo-symbol"})
            caixa_simbolo.text = simbolo
            caixa_simbolo.tail = " "
            item.insert(0, caixa_simbolo)
        return root


class _ExtensaoAtividades(Extension):
    def extendMarkdown(self, md):  # noqa: N802 - API do python-markdown
        md.treeprocessors.register(_ItemDeAtividade(md), "todonow_atividades", 1)


def render(texto: str | None) -> Markup:
    """Markdown → HTML seguro (pronto para o template)."""
    if not texto or not texto.strip():
        return Markup("")

    bruto = _markdown.markdown(
        texto,
        extensions=[*MARKDOWN_EXTENSIONS, _ExtensaoAtividades()],
        output_format="html",
    )
    limpo = nh3.clean(
        bruto,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_SCHEMES,
        link_rel="noopener noreferrer nofollow",
    )
    return Markup(limpo)


@lru_cache(maxsize=512)
def render_cached(texto: str) -> Markup:
    """Igual a :func:`render`, com cache (as mesmas notas aparecem em várias telas)."""
    return render(texto)


def para_texto(texto: str | None, limite: int = 160) -> str:
    """Versão em texto puro, para resumos na lista de tarefas."""
    if not texto or not texto.strip():
        return ""

    html = render(texto)
    sem_tags = re.sub(r"<[^>]+>", " ", str(html))
    limpo = re.sub(r"\s+", " ", sem_tags).strip()
    limpo = (
        limpo.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    if len(limpo) <= limite:
        return limpo
    return limpo[: limite - 1].rstrip() + "…"


def tem_conteudo(texto: str | None) -> bool:
    return bool(texto and texto.strip())
