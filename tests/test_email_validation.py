"""Validação de e-mail: aceita domínios internos de homelab, recusa lixo."""

from __future__ import annotations

import pytest

from app.utils import is_valid_email, is_wellformed_email

ACEITOS = [
    "familia@casa.local",
    "admin@homelab.local",
    "eu@localhost",
    "ti@nas",
    "ana@example.com",
    "maria.silva@empresa.com.br",
    "user+tag@dominio.com",
    "",
    "   ",
]

REJEITADOS = [
    "invalido",
    "sem-arroba.com",
    "a@b",
    "@semlocal.com",
    "x@",
    "a@@b.com",
    "com espaco@x.com",
    ".inicio@casa.local",
    "fim.@casa.local",
    "dois..pontos@casa.local",
    "x@-casa.local",
]


@pytest.mark.parametrize("value", ACEITOS)
def test_emails_aceitos(value):
    assert is_valid_email(value) is True


@pytest.mark.parametrize("value", REJEITADOS)
def test_emails_rejeitados(value):
    assert is_valid_email(value) is False


@pytest.mark.parametrize(
    "value",
    ["familia@casa.local", "admin@homelab.local", "eu@localhost", "ti@nas"],
)
def test_dominios_internos_passam_pela_checagem_tolerante(value):
    """Sem ponto no domínio (``nas``) ou de uso especial (``.local``): aceitos."""
    assert is_wellformed_email(value) is True


def test_checagem_tolerante_ainda_recusa_lixo():
    assert is_wellformed_email("invalido") is False
    assert is_wellformed_email("a@b") is False
    assert is_wellformed_email("com espaco@x.com") is False
