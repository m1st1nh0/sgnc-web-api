from datetime import date

from fastapi import APIRouter, Depends, Query, Response

from app.auth import UsuarioLogado, exigir_gestao
from app import relatorios_service


router = APIRouter(prefix="/relatorios", tags=["relatórios"])


@router.get("/ncs.csv")
def exportar_ncs_csv(
    usuario: UsuarioLogado = Depends(exigir_gestao),
    inicio: date | None = Query(default=None),
    fim: date | None = Query(default=None),
    status: str | None = Query(default=None),
    colaborador_id: str | None = Query(default=None),
    setor: str | None = Query(default=None),
):
    """Exporta NCs do escopo gerencial em CSV UTF-8 compatível com Excel."""
    conteudo, nome = relatorios_service.gerar_csv_detalhado(
        usuario,
        inicio,
        fim,
        status_filtro=status,
        colaborador_id=colaborador_id,
        setor=setor,
    )
    return Response(
        content=conteudo,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{nome}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/resumo.pdf")
def exportar_resumo_pdf(
    usuario: UsuarioLogado = Depends(exigir_gestao),
    inicio: date | None = Query(default=None),
    fim: date | None = Query(default=None),
):
    """Exporta o resumo gerencial do contrato Insights V2 em PDF."""
    conteudo, nome = relatorios_service.gerar_pdf_resumo(
        usuario,
        inicio,
        fim,
    )
    return Response(
        content=conteudo,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{nome}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
