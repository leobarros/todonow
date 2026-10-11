"""Mobile/desktop, instalação como app e acesso remoto (Tailscale/proxy)."""

from __future__ import annotations

from flask import request

from app import create_app
from app.extensions import db

# --------------------------------------------------------------------------
# Mobile: manifest, ícone e ajustes de toque
# --------------------------------------------------------------------------


def test_paginas_apontam_para_o_manifesto_e_o_icone(auth_client):
    body = auth_client.get("/").data.decode("utf-8")
    assert 'rel="manifest"' in body
    assert "manifest.webmanifest" in body
    assert 'rel="icon"' in body and "img/icon.svg" in body
    assert 'name="apple-mobile-web-app-capable"' in body
    assert 'name="theme-color"' in body


def test_manifest_e_servido_com_o_tipo_certo(auth_client):
    resposta = auth_client.get("/static/manifest.webmanifest")
    assert resposta.status_code == 200
    # sem o mimetype certo o navegador não oferece "instalar na tela inicial"
    assert resposta.mimetype == "application/manifest+json"
    dados = resposta.get_json()
    assert dados["short_name"] == "todonow"
    assert dados["display"] == "standalone"
    assert dados["start_url"] == "/"
    assert dados["icons"][0]["src"].endswith("icon.svg")


def test_icone_svg_e_servido(auth_client):
    resposta = auth_client.get("/static/img/icon.svg")
    assert resposta.status_code == 200
    assert "image/svg" in resposta.mimetype
    assert b"<svg" in resposta.data


def test_css_cobre_toque_e_zoom_do_ios(auth_client):
    css = auth_client.get("/static/css/todonow.css").data.decode("utf-8")

    # iOS dá zoom ao focar campo com fonte < 16px: no celular os campos vão a 1rem.
    assert "@media (max-width: 767.98px)" in css
    assert ".input-group-text { font-size: 1rem; }" in css

    # Alvos de toque das ações da linha (~44px).
    assert "min-width: 2.75rem;" in css
    assert "min-height: 2.75rem;" in css

    # Tabelas densas continuam compactas.
    assert ".table .btn-sm { min-height: 0; }" in css


def test_viewport_esta_declarado(auth_client):
    body = auth_client.get("/").data.decode("utf-8")
    assert 'name="viewport"' in body and "width=device-width" in body


# --------------------------------------------------------------------------
# Acesso remoto: Tailscale (proxy) e Host confiável
# --------------------------------------------------------------------------


def _app_isolado(tmp_path, **overrides):
    aplicacao = create_app(
        "testing",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'proxy.db'}",
        BACKUP_DIR=tmp_path / "bk",
        SECRET_KEY="teste-proxy",
        WTF_CSRF_ENABLED=False,
        **overrides,
    )

    @aplicacao.get("/__probe")
    def probe():  # pragma: no cover - usado só aqui
        return {"scheme": request.scheme, "host": request.host}

    return aplicacao


def test_sem_trust_proxy_ignora_x_forwarded(tmp_path):
    aplicacao = _app_isolado(tmp_path)
    with aplicacao.app_context():
        db.create_all()
        dados = aplicacao.test_client().get(
            "/__probe",
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "todonow.minha-tailnet.ts.net"},
        ).get_json()
    assert dados["scheme"] == "http"
    assert "todonow.minha-tailnet.ts.net" not in dados["host"]


def test_trust_proxy_respeita_https_do_tailscale(tmp_path):
    """Com Tailscale Serve na frente, o app precisa enxergar https e o host real."""
    aplicacao = _app_isolado(tmp_path, TRUST_PROXY=True)
    with aplicacao.app_context():
        db.create_all()
        dados = aplicacao.test_client().get(
            "/__probe",
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "todonow.minha-tailnet.ts.net"},
        ).get_json()
    assert dados["scheme"] == "https"
    assert dados["host"] == "todonow.minha-tailnet.ts.net"


def test_trusted_hosts_bloqueia_host_desconhecido(tmp_path):
    aplicacao = _app_isolado(tmp_path, TRUSTED_HOSTS=["todonow.minha-tailnet.ts.net"])
    with aplicacao.app_context():
        db.create_all()
        cliente = aplicacao.test_client()

        assert cliente.get("/healthz").status_code == 400  # host local recusado
        aceito = cliente.get("/healthz", headers={"Host": "todonow.minha-tailnet.ts.net"})
        assert aceito.status_code == 200


def test_env_example_documenta_o_acesso_remoto():
    from pathlib import Path

    exemplo = (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")
    for chave in ("TODONOW_BIND", "TRUST_PROXY", "TRUST_PROXY_HOPS", "TRUSTED_HOSTS", "SESSION_COOKIE_SECURE"):
        assert chave in exemplo, chave


def test_compose_permite_amarrar_a_interface():
    """O bind e as portas são variáveis: dá para expor só no tailnet e trocar a porta."""
    from pathlib import Path

    compose = (Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${TODONOW_BIND:-0.0.0.0}:${TODONOW_PORT:-8081}:${TODONOW_CONTAINER_PORT:-8081}" in compose
