from datetime import date

from fastapi import APIRouter, Depends, Query

from app.auth import UsuarioLogado, exigir_gestao
from app import insights_service_v2 as insights_base
from app import insights_service_pr04 as insights_service

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("")
def obter(
    usuario: UsuarioLogado = Depends(exigir_gestao),
    inicio: date | None = Query(
        default=None,
        description="Início do período (YYYY-MM-DD). Padrão: 12 meses de calendário atrás.",
    ),
    fim: date | None = Query(
        default=None,
        description="Fim do período (YYYY-MM-DD). Padrão: hoje.",
    ),
):
    """Insights V2: ADM global; supervisor somente subordinados diretos."""
    # Mantém o ponto de injeção histórico usado pelas regressões PR01/PR02.
    # Em produção ambos apontam para o mesmo cliente service_role; em testes,
    # o mock aplicado no serviço V2 também alimenta o contrato PR04.
    insights_service.cliente_servico = insights_base.cliente_servico
    return insights_service.obter_insights(usuario, inicio, fim)
