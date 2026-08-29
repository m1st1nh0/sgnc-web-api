"""Contrato canônico de reincidência da PR02.

A fonte de verdade da ocorrência é ``nc_causas.ocorrencia_numero``. O campo
``nao_conformidades.reincidencia`` permanece apenas como projeção legada para
compatibilidade visual durante a transição.
"""
from datetime import date


STATUS_QUE_CONTAM_REINCIDENCIA = (
    "validada",  # legado de rollout
    "aguardando_analise",  # legado de rollout
    "aguardando_feedback",
    "aguardando_aceite",
    "concluida",
)


def inicio_janela_12_meses(referencia: date) -> date:
    """Subtrai 12 meses de calendário, reproduzindo a semântica do Postgres.

    ``2024-02-29 - 12 meses`` resulta em ``2023-02-28``. Isso evita a antiga
    aproximação por 365 dias, que erra em anos bissextos.
    """
    try:
        return referencia.replace(year=referencia.year - 1)
    except ValueError:
        return referencia.replace(year=referencia.year - 1, day=28)


def eh_reincidencia(ocorrencia_numero: int | None) -> bool:
    """Uma causa é reincidente a partir da segunda ocorrência na janela."""
    return ocorrencia_numero is not None and ocorrencia_numero > 1
