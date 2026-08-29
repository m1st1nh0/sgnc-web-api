from fastapi import APIRouter, Depends

from app.auth import (
    UsuarioLogado,
    exigir_adm,
    exigir_senha_definitiva,
)
from app.schemas_nc import (
    MedidaDisciplinarEntrada,
    NcAceite,
    NcAvaliar,
    NcEntrada,
    NcFeedback,
)
from app import nc_service_pr02 as nc_service
from app.supabase_client import cliente_do_usuario

router = APIRouter(prefix="/nc", tags=["não conformidades"])


@router.get("")
def listar(usuario: UsuarioLogado = Depends(exigir_senha_definitiva)):
    """Lista as NCs visíveis ao usuário conforme o RLS."""
    return nc_service.listar_ncs(usuario)


@router.get("/causas")
def causas_conhecidas(
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
):
    cliente = cliente_do_usuario(usuario.token)
    return nc_service.listar_causas_conhecidas(cliente)


@router.get("/{nc_id}")
def buscar(
    nc_id: int,
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
):
    return nc_service.buscar_nc(usuario, nc_id)


@router.post("")
def abrir(
    dados: NcEntrada,
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
):
    """Qualquer usuário autenticado com senha definitiva pode abrir uma NC."""
    return nc_service.criar_nc(usuario, dados)


@router.put("/{nc_id}")
def editar(
    nc_id: int,
    dados: NcEntrada,
    usuario: UsuarioLogado = Depends(exigir_adm),
):
    return nc_service.atualizar_nc(usuario, nc_id, dados)


@router.delete("/{nc_id}")
def excluir(nc_id: int, usuario: UsuarioLogado = Depends(exigir_adm)):
    nc_service.excluir_nc(usuario, nc_id)
    return {"status": "excluida"}


@router.post("/{nc_id}/avaliar")
def avaliar(
    nc_id: int,
    dados: NcAvaliar,
    usuario: UsuarioLogado = Depends(exigir_adm),
):
    """ADM decide: aberta -> aguardando_feedback | invalidada."""
    return nc_service.avaliar_nc(usuario, nc_id, dados)


@router.post("/{nc_id}/enviar", deprecated=True)
def enviar_legado(
    nc_id: int,
    usuario: UsuarioLogado = Depends(exigir_adm),
):
    """Compatibilidade temporária: validada (legado) -> aguardando_feedback."""
    return nc_service.enviar_nc(usuario, nc_id)


@router.post("/{nc_id}/feedback")
def feedback(
    nc_id: int,
    dados: NcFeedback,
    usuario: UsuarioLogado = Depends(exigir_adm),
):
    """ADM registra feedback: aguardando_feedback -> aguardando_aceite."""
    return nc_service.aplicar_feedback(usuario, nc_id, dados)


@router.post("/{nc_id}/aceitar")
def aceitar(
    nc_id: int,
    dados: NcAceite,
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
):
    """Colaborador alvo confirma: aguardando_aceite -> concluida."""
    return nc_service.aceitar_nc(usuario, nc_id, dados)


@router.post("/medidas-disciplinares", status_code=201)
def registrar_medida_disciplinar(
    dados: MedidaDisciplinarEntrada,
    usuario: UsuarioLogado = Depends(exigir_adm),
):
    return nc_service.registrar_medida_disciplinar(usuario, dados)
