"""
Modulo compartilhado de log operacional da pipeline.

Todos os eventos de todas as etapas sao gravados na tabela DynamoDB
definida em TABELA_LOG. Modelo de dados:

    PK  id_execucao  -> nome da execucao da Step Function
    SK  id_evento    -> "<timestamp>#<etapa>#<uf>"  (ordena cronologicamente)
    GSI idx_status   -> status + data_hora  (para varrer falhas de todas as execucoes)

O item "0000#RESUMO" e' gravado pela lambda de controle e concentra o
resultado consolidado da execucao.
"""

import functools
import os
import time
import traceback
from datetime import datetime, timezone
from decimal import Decimal

import boto3

TABELA_LOG = os.environ.get("TABELA_LOG", "tcc-pipeline-logs")
TTL_DIAS = int(os.environ.get("TTL_DIAS", "90"))

# A tabela e' resolvida de forma preguicosa: nada de I/O ou de acesso a
# configuracao durante o import, para que um problema de ambiente nunca
# derrube o runtime na inicializacao (Runtime.ExitError).
_tabela = None


def _obter_tabela():
    global _tabela
    if _tabela is None:
        _tabela = boto3.resource("dynamodb").Table(TABELA_LOG)
    return _tabela

STATUS_INICIADO = "INICIADO"
STATUS_SUCESSO = "SUCESSO"
STATUS_ERRO = "ERRO"


def agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def data_referencia() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _sanitizar(valor):
    """DynamoDB nao aceita float; converte recursivamente para Decimal."""
    if isinstance(valor, float):
        return Decimal(str(round(valor, 6)))
    if isinstance(valor, dict):
        return {k: _sanitizar(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_sanitizar(v) for v in valor]
    if isinstance(valor, (str, int, bool, Decimal)) or valor is None:
        return valor
    return str(valor)


def registrar(id_execucao, etapa, status, uf=None, mensagem=None,
              metricas=None, id_evento=None):
    """Grava um evento de log. Nunca lanca excecao para nao derrubar a etapa."""
    ts = agora_iso()
    uf = uf or "GERAL"
    item = {
        "id_execucao": str(id_execucao),
        "id_evento": id_evento or f"{ts}#{etapa}#{uf}",
        "etapa": etapa,
        "status": status,
        "uf": uf,
        "data_hora": ts,
        "data_referencia": data_referencia(),
        "ttl": int(time.time()) + TTL_DIAS * 86400,
    }
    if mensagem:
        item["mensagem"] = str(mensagem)[:3500]
    if metricas:
        item["metricas"] = _sanitizar(metricas)

    try:
        _obter_tabela().put_item(Item=item)
    except Exception as erro:  # log nunca pode quebrar a pipeline
        print(f"[WARN] falha ao gravar log no DynamoDB: {erro}")
    print(f"[LOG] {status} | {etapa} | {uf} | {mensagem or ''}")
    return item


def etapa_monitorada(nome_etapa):
    """
    Decorator para handlers de lambda.

    Grava INICIADO antes de executar e SUCESSO/ERRO depois, com duracao.
    O handler decorado deve retornar um dict; as chaves numericas do retorno
    sao usadas como metricas do log.
    """
    def decorador(handler):
        @functools.wraps(handler)
        def wrapper(event, context):
            id_execucao = event.get("id_execucao", "SEM-EXECUCAO")
            uf = (event.get("uf") or "").upper() or None
            inicio = time.time()

            registrar(id_execucao, nome_etapa, STATUS_INICIADO, uf=uf,
                      mensagem=f"entrada={ {k: v for k, v in event.items() if k != 'payload'} }")
            try:
                resultado = handler(event, context) or {}
                duracao = int((time.time() - inicio) * 1000)
                metricas = {k: v for k, v in resultado.items()
                            if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)}
                metricas["duracao_ms"] = duracao
                registrar(id_execucao, nome_etapa, STATUS_SUCESSO, uf=uf,
                          mensagem=resultado.get("mensagem"), metricas=metricas)
                resultado.setdefault("status", STATUS_SUCESSO)
                resultado.setdefault("etapa", nome_etapa)
                resultado.setdefault("id_execucao", id_execucao)
                if uf:
                    resultado.setdefault("uf", uf)
                return resultado
            except Exception as erro:
                duracao = int((time.time() - inicio) * 1000)
                registrar(id_execucao, nome_etapa, STATUS_ERRO, uf=uf,
                          mensagem=f"{type(erro).__name__}: {erro}",
                          metricas={"duracao_ms": duracao})
                print(traceback.format_exc())
                raise
        return wrapper
    return decorador