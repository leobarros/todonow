"""Infraestrutura de PWA/offline: service worker, página offline e versionamento.

O service worker precisa ser servido da **raiz** (``/sw.js``) para poder
controlar todas as páginas do app — um arquivo em ``/static/sw.js`` só teria
escopo em ``/static/``. Por isso ele é um template, e não um arquivo estático:
aproveitamos para injetar a lista de assets e a versão do cache, calculada a
partir do conteúdo real de ``app/static``. Assim que qualquer CSS/JS muda, a
versão muda, o navegador percebe que o ``sw.js`` é outro e troca o cache velho
por um novo — sem ninguém precisar lembrar de subir um número à mão.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from flask import Flask, Response, render_template, url_for

# Rotas que nunca entram no cache: dependem do servidor (login, backup), não
# fazem sentido offline (healthcheck, o próprio service worker) ou são a
# própria tela de "sem conexão", que já vive precacheada no cache do shell.
NEVER_CACHE_PREFIXES = ("/auth/", "/backup/", "/healthz", "/sw.js", "/offline")


def _static_files(static_folder: str) -> list[str]:
    """Todos os arquivos servidos em ``/static``, em ordem estável.

    Precachear a pasta inteira (~1 MB, Bootstrap + ícones + FullCalendar) é o
    que faz o app abrir de verdade sem rede — inclusive o calendário, que sem
    isso só funcionaria depois de visitado uma vez online.
    """
    root = Path(static_folder)
    if not root.is_dir():  # pragma: no cover - instalação quebrada
        return []
    arquivos = [
        str(caminho.relative_to(root)).replace("\\", "/")
        for caminho in root.rglob("*")
        if caminho.is_file() and caminho.suffix != ".map"
    ]
    return sorted(arquivos)


def _fingerprint(static_folder: str, app_version: str) -> str:
    """Hash do conjunto de assets (nome + tamanho + mtime).

    Usa mtime/tamanho em vez de ler os arquivos: o resultado é o mesmo a cada
    boot e não custa 1 MB de I/O por partida.
    """
    digest = hashlib.sha256(app_version.encode("utf-8"))
    root = Path(static_folder)
    for relative in _static_files(static_folder):
        try:
            stat = (root / relative).stat()
        except OSError:  # pragma: no cover - arquivo removido no meio do caminho
            continue
        digest.update(f"{relative}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()[:12]


def register_pwa(app: Flask) -> None:
    """Registra ``/sw.js`` e ``/offline`` e guarda a versão dos assets."""
    cache: dict[str, object] = {}

    def version() -> str:
        if "version" not in cache:
            cache["version"] = _fingerprint(app.static_folder, str(app.config.get("APP_VERSION", "")))
        return str(cache["version"])

    def shell_urls() -> list[str]:
        if "shell" not in cache:
            cache["shell"] = [url_for("static", filename=arquivo) for arquivo in _static_files(app.static_folder)]
        return list(cache["shell"])  # type: ignore[arg-type]

    @app.get("/sw.js")
    def service_worker():
        """Serve o worker na raiz, com escopo liberado para todo o site."""
        corpo = render_template(
            "sw.js",
            version=version(),
            app_version=str(app.config.get("APP_VERSION", "")),
            shell_urls=shell_urls(),
            offline_url=url_for("offline_page"),
            never_cache=list(NEVER_CACHE_PREFIXES),
        )
        resposta = Response(corpo, mimetype="application/javascript")
        resposta.headers["Service-Worker-Allowed"] = "/"
        # O navegador precisa enxergar mudanças no worker imediatamente.
        resposta.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resposta

    @app.get("/offline")
    def offline_page():
        """Página autossuficiente mostrada quando não há rede nem cache.

        Não estende ``base.html`` de propósito: ela precisa funcionar mesmo sem
        CSS, fonte ou JavaScript disponíveis.
        """
        resposta = Response(render_template("offline.html", app_name=app.config.get("APP_NAME", "todonow")))
        resposta.headers["Cache-Control"] = "no-cache"
        return resposta
