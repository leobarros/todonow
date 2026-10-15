"""Suporte ao modo offline: idempotência das ações reenviadas.

O modo offline do todonow funciona assim: quando a rede cai (ou o usuário
fecha a aba antes de a resposta chegar), a ação é guardada no navegador e
reenviada depois. Reenviar é seguro para operações que *definem* um valor
("a data é 12/03", "o status é concluída"), mas não para operações que
*criam* algo. Se o servidor já tiver criado a tarefa e só a resposta se
perdeu, o reenvio criaria uma duplicata.

Para isso o navegador manda um cabeçalho ``X-Idempotency-Key`` (um UUID por
ação). O servidor guarda a resposta aplicada em ``client_operations``; se a
mesma chave voltar, devolve a resposta original em vez de executar de novo.

O registro entra na **mesma transação** do efeito: ou os dois são gravados, ou
nenhum. Assim nunca existe "tarefa criada sem registro da chave", que é
exatamente o caso que geraria duplicidade.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from flask import Request, Response, jsonify, request
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from .extensions import db
from .models import ClientOperation
from .utils import now_local

logger = logging.getLogger(__name__)

# O JS offline manda no cabeçalho; o campo de formulário é aceito como
# alternativa para depurar o reenvio à mão (curl, testes).
IDEMPOTENCY_HEADER = "X-Idempotency-Key"
IDEMPOTENCY_FIELD = "idempotency_key"

# Mesmo uma chave "sempre igual" por engano não pode virar string gigante.
MAX_KEY_LENGTH = 80
# Depois disso a ação não é mais reenviada por ninguém: é só histórico morto.
RETENTION_DAYS = 7


def idempotency_key(req: Request | None = None) -> str | None:
    """Lê a chave de idempotência da requisição (None quando não há)."""
    req = req or request
    raw = req.headers.get(IDEMPOTENCY_HEADER) or req.form.get(IDEMPOTENCY_FIELD)
    if not raw:
        return None
    key = str(raw).strip()[:MAX_KEY_LENGTH]
    return key or None


def replay(key: str | None, endpoint: str) -> Response | None:
    """Devolve a resposta já aplicada para *key*, ou None se for a primeira vez.

    Só o cliente offline manda a chave, então a resposta é sempre JSON.
    """
    if not key:
        return None
    record = _find(key, endpoint)
    if record is None:
        return None
    return jsonify(record.payload()), record.status_code


def remember(key: str | None, endpoint: str, payload: dict[str, Any] | None, status: int = 200) -> None:
    """Registra a ação aplicada. **Não** faz commit: soma-se à transação do
    efeito para que os dois sejam gravados juntos."""
    if not key or not current_user.is_authenticated:
        return
    db.session.add(
        ClientOperation(
            user_id=current_user.id,
            key=key,
            endpoint=endpoint,
            status_code=status,
            response_json=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
        )
    )


def commit(key: str | None = None, endpoint: str | None = None) -> None:
    """Commita a transação, sobrevivendo à corrida de dois reenvios iguais.

    Se outra requisição com a mesma chave commitou primeiro, a chave única
    estoura: nesse caso o efeito *dela* é o que vale e o trabalho desta é
    descartado. Se a chave nem existe no banco, o erro é outro (violação de
    verdade) e sobe.
    """
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if not key or _find(key, endpoint) is None:
            raise
        return

    if key:
        # Limpeza oportunista: a tabela é pequena e isto usa índice. Fica depois
        # do commit para não disparar autoflush no meio da transação do efeito.
        try:
            prune()
            db.session.commit()
        except Exception:  # pragma: no cover - limpeza nunca derruba o pedido
            db.session.rollback()
            logger.warning("Não foi possível podar as chaves de idempotência antigas", exc_info=True)


def prune(retention_days: int = RETENTION_DAYS) -> int:
    """Apaga registros antigos. Chamado a cada gravação: a tabela é pequena e o
    DELETE usa índice."""
    cutoff = now_local() - timedelta(days=retention_days)
    deleted = ClientOperation.query.filter(ClientOperation.created_at < cutoff).delete(synchronize_session=False)
    return int(deleted or 0)


def _find(key: str, endpoint: str | None = None) -> ClientOperation | None:
    if not current_user.is_authenticated:
        return None
    query = ClientOperation.query.filter_by(user_id=current_user.id, key=key)
    if endpoint is not None:
        query = query.filter_by(endpoint=endpoint)
    return query.first()
