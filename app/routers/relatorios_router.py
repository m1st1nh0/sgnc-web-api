from datetime import date

from fastapi import APIRouter, Depends, Query, Response

from app.auth import UsuarioLogado, exigir_gestao, exigir_senha_definitiva
from app import relatorios_service, relatorio_documentos


router = APIRouter(prefix="/relatorios", tags=["relatórios"])


def _pdf_response(conteudo: bytes, nome: str) -> Response:
    return Response(
        content=conteudo,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{nome}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/usuarios/{usuario_id}/dossie.pdf")
def exportar_dossie_colaborador(
    usuario_id: str,
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
):
    """Baixa o resumo individual autorizado do colaborador em PDF."""
    conteudo, nome = relatorio_documentos.gerar_pdf_dossie(usuario, usuario_id)
    return _pdf_response(conteudo, nome)


@router.get("/nc/{nc_id}.pdf")
def exportar_relatorio_nc(
    nc_id: int,
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
):
    """Baixa a NC autorizada com evidências incorporadas ao documento."""
    conteudo, nome = relatorio_documentos.gerar_pdf_nc(usuario, nc_id)
    return _pdf_response(conteudo, nome)


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
    return _pdf_response(conteudo, nome)
