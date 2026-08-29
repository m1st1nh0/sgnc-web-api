from datetime import date

from fastapi import APIRouter, Depends, Query

from app.auth import UsuarioLogado, exigir_gestao
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
    return insights_service.obter_insights(usuario, inicio, fim)
