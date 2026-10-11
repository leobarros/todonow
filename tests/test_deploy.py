"""Guardas de empacotamento: imagem publicada, compose e segredos.

Nada aqui depende de Docker rodando — são checagens de configuração, que é onde
um erro só aparece na hora de subir no homelab.
"""

from __future__ import annotations

from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _texto(relativo: str) -> str:
    return (RAIZ / relativo).read_text(encoding="utf-8")


def test_compose_usa_a_imagem_latest_e_sempre_confere():
    """Padrão: tag `latest` com pull a cada `up`, para pegar versão nova sozinho."""
    compose = _texto("docker-compose.yml")
    assert "image: ${TODONOW_IMAGE:-nickolback/todonow:latest}" in compose
    assert "pull_policy: ${TODONOW_PULL_POLICY:-always}" in compose


def test_compose_de_producao_nao_constroi():
    """O `up` do dia a dia não pode construir por engano."""
    compose = _texto("docker-compose.yml")
    assert "build:" not in compose
    assert "dockerfile: Dockerfile" not in compose


def test_build_fica_no_override():
    override = _texto("docker-compose.build.yml")
    assert "build:" in override
    assert "context: ." in override
    assert "dockerfile: Dockerfile" in override


def test_makefile_tem_os_fluxos_de_imagem():
    makefile = _texto("Makefile")
    for alvo in ("up:", "build:", "up-build:", "push:", "release:", "dev:"):
        assert alvo in makefile, alvo
    assert "IMAGE ?= $(REGISTRY)/$(IMAGE_NAME):$(TAG)" in makefile
    assert "TAG ?= latest" in makefile
    assert "docker push $(IMAGE)" in makefile
    assert "docker-compose.build.yml" in makefile


def test_env_example_documenta_a_imagem():
    exemplo = _texto(".env.example")
    assert "TODONOW_IMAGE=nickolback/todonow:latest" in exemplo
    assert "TODONOW_PULL_POLICY=always" in exemplo


def test_dev_override_usa_o_codigo_local():
    dev = _texto("docker-compose.dev.yml")
    assert "flask" in dev and "--debug" in dev
    assert ".:/app" in dev


def test_imagem_nao_leva_segredos_nem_lixo():
    ignorados = _texto(".dockerignore")
    for padrao in (".env", "instance", "data", "tests", "__pycache__"):
        assert any(linha.strip() == padrao for linha in ignorados.splitlines()), padrao


def test_dockerfile_roda_como_usuario_comum_e_tem_healthcheck():
    dockerfile = _texto("Dockerfile")
    assert "USER todonow" in dockerfile
    assert "useradd" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "healthz" in dockerfile
    assert "tini" in dockerfile  # PID 1 tratando sinais


def test_requirements_tem_o_que_a_imagem_precisa():
    req = _texto("requirements.txt")
    for pacote in ("Flask", "Flask-Login", "Flask-Migrate", "gunicorn", "Markdown", "nh3"):
        assert pacote in req, pacote


def test_a_stack_inteira_fala_8081():
    """A porta do projeto é 8081 de ponta a ponta: imagem, entrypoint, execução
    local, compose, CI e Makefile. Nenhuma camada pode voltar para outra porta."""
    assert "PORT=8081" in _texto("Dockerfile")
    assert "EXPOSE 8081" in _texto("Dockerfile")
    assert 'PORT="${PORT:-8081}"' in _texto("docker/entrypoint.sh")
    assert 'os.environ.get("PORT", 8081)' in _texto("wsgi.py")

    compose = _texto("docker-compose.yml")
    assert "PORT: ${TODONOW_CONTAINER_PORT:-8081}" in compose
    assert '${TODONOW_PORT:-8081}:${TODONOW_CONTAINER_PORT:-8081}"' in compose
    assert "127.0.0.1:${TODONOW_CONTAINER_PORT:-8081}/healthz" in compose

    assert "--port=${TODONOW_CONTAINER_PORT:-8081}" in _texto("docker-compose.dev.yml")
    assert "--port 8081" in _texto("Makefile")
    assert "TODONOW_PORT=8081" in _texto(".env.example")
    assert "TODONOW_CONTAINER_PORT=8081" in _texto(".env.example")

    ci = _texto(".github/workflows/ci.yml")
    assert "8081:8081" in ci
    assert "127.0.0.1:8081/healthz" in ci


def test_nenhuma_referencia_a_8000_na_configuracao():
    """8000 é do outro projeto do homelab: não pode voltar para cá."""
    arquivos = (
        "Dockerfile",
        "docker/entrypoint.sh",
        "wsgi.py",
        "docker-compose.yml",
        "docker-compose.dev.yml",
        "docker-compose.build.yml",
        ".env.example",
        "Makefile",
        ".github/workflows/ci.yml",
    )
    for arquivo in arquivos:
        assert "8000" not in _texto(arquivo), arquivo
