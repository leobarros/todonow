"""Partes em JavaScript conferidas pelo node que já existe na máquina.

O projeto não tem (nem precisa de) `package.json` e companhia: em vez de trazer
uma infraestrutura de JS, estes testes rodam o `node` do sistema e **pulam**
quando ele não está instalado. Assim a lógica que decide o comportamento
offline — o que o service worker guarda e o que a fila faz com cada ação —
fica coberta de verdade onde há node, e o resto da suíte nunca depende dele.

Os harnesses vivem em ``tests/js/``:
  * ``sw_harness.mjs``      — cache do service worker (install/activate/fetch);
  * ``offline_harness.mjs`` — regras puras de ``app/static/js/offline-core.js``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
JS = RAIZ / "app" / "static" / "js"
HARNESSES = RAIZ / "tests" / "js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node não está disponível")


def _node(*argumentos: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [NODE, *argumentos],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(RAIZ),
    )


def test_service_worker_passa_no_harness(client, tmp_path):
    """Install, activate e as decisões de cache do sw.js, executadas de verdade."""
    renderizado = client.get("/sw.js").data.decode("utf-8")
    arquivo = tmp_path / "sw.js"
    arquivo.write_text(renderizado, encoding="utf-8")

    resultado = _node(str(HARNESSES / "sw_harness.mjs"), str(arquivo))

    assert resultado.returncode == 0, f"{resultado.stdout}\n{resultado.stderr}"
    assert "verificações passaram" in resultado.stdout
    assert "FALHA" not in resultado.stdout


def test_regras_do_offline_passam_no_harness():
    """Espera entre tentativas, anulação de repetidas, sessão e rascunho."""
    resultado = _node(str(HARNESSES / "offline_harness.mjs"), str(JS / "offline-core.js"))

    assert resultado.returncode == 0, f"{resultado.stdout}\n{resultado.stderr}"
    assert "verificações passaram" in resultado.stdout
    assert "FALHA" not in resultado.stdout


def test_arquivos_js_nao_tem_erro_de_sintaxe():
    """Erro de sintaxe em JS só apareceria no navegador, já em produção."""
    arquivos = sorted(JS.glob("*.js"))
    assert arquivos, "app/static/js não pode estar vazio"

    for arquivo in arquivos:
        resultado = _node("--check", str(arquivo))
        assert resultado.returncode == 0, f"{arquivo.name}: {resultado.stderr}"


def test_nucleo_do_offline_carrega_antes_do_resto(client):
    """offline.js depende de offline-core.js; a ordem dos <script> importa."""
    base = (RAIZ / "app" / "templates" / "base.html").read_text(encoding="utf-8")

    assert base.index("js/offline-core.js") < base.index("js/offline.js")


def test_todo_js_do_app_e_precacheado(client, app):
    """Um arquivo novo em app/static que fique fora do cache abre quebrado."""
    from app.pwa import _static_files

    corpo = client.get("/sw.js").data.decode("utf-8")
    for relativo in _static_files(app.static_folder):
        assert f'"/static/{relativo}"' in corpo, relativo

    # Em especial: os dois módulos do modo offline.
    assert "/static/js/offline-core.js" in corpo
    assert "/static/js/offline.js" in corpo
