from fastapi import APIRouter, Depends

from app.auth import UsuarioLogado, usuario_atual, exigir_adm
from app.schemas_nc import NcEntrada, NcAvaliar, NcFeedback, NcAceite
from app import nc_service
from app.supabase_client import cliente_do_usuario

router = APIRouter(prefix="/nc", tags=["não conformidades"])


@router.get("")
def listar(usuario: UsuarioLogado = Depends(usuario_atual)):
    """Lista as NCs visíveis para o usuário logado.
    - Autor sempre vê o que abriu (mesmo antes da avaliação do ADM)
    - Colaborador (alvo) e seu supervisor veem a partir de 'validada'
    - ADM vê tudo
    Tudo isso é garantido pelo RLS no banco, não por lógica aqui."""
    return nc_service.listar_ncs(usuario)


@router.get("/causas")
def causas_conhecidas(usuario: UsuarioLogado = Depends(usuario_atual)):
    """Lista de causas já cadastradas, para alimentar o autocomplete."""
    cliente = cliente_do_usuario(usuario.token)
    return nc_service.listar_causas_conhecidas(cliente)


@router.get("/{nc_id}")
def buscar(nc_id: int, usuario: UsuarioLogado = Depends(usuario_atual)):
    return nc_service.buscar_nc(usuario, nc_id)


@router.post("")
def abrir(dados: NcEntrada, usuario: UsuarioLogado = Depends(usuario_atual)):
    """Qualquer usuário autenticado pode abrir uma NC (nasce em 'aberta',
    aguardando avaliação do ADM sobre se é procedente)."""
    return nc_service.criar_nc(usuario, dados)


@router.put("/{nc_id}")
def editar(nc_id: int, dados: NcEntrada, usuario: UsuarioLogado = Depends(exigir_adm)):
    """Edição de campos livres. Fora do escopo deste endpoint estão as
    transições de status, que têm endpoints próprios abaixo."""
    return nc_service.atualizar_nc(usuario, nc_id, dados)


@router.delete("/{nc_id}")
def excluir(nc_id: int, usuario: UsuarioLogado = Depends(exigir_adm)):
    nc_service.excluir_nc(usuario, nc_id)
    return {"status": "excluida"}


@router.post("/{nc_id}/avaliar")
def avaliar(nc_id: int, dados: NcAvaliar, usuario: UsuarioLogado = Depends(exigir_adm)):
    """ADM decide se a NC é procedente: aberta -> validada | invalidada."""
    return nc_service.avaliar_nc(usuario, nc_id, dados)


@router.post("/{nc_id}/enviar")
def enviar(nc_id: int, usuario: UsuarioLogado = Depends(exigir_adm)):
    """validada -> aguardando_analise (colaborador e supervisor passam a ver)."""
    return nc_service.enviar_nc(usuario, nc_id)


@router.post("/{nc_id}/feedback")
def feedback(nc_id: int, dados: NcFeedback, usuario: UsuarioLogado = Depends(exigir_adm)):
    """ADM aplica o parecer/combinado: aguardando_analise -> aguardando_aceite."""
    return nc_service.aplicar_feedback(usuario, nc_id, dados)


@router.post("/{nc_id}/aceitar")
def aceitar(nc_id: int, dados: NcAceite, usuario: UsuarioLogado = Depends(usuario_atual)):
    """Aceite formal do colaborador (precisa digitar a frase de
    confirmação exata): aguardando_aceite -> concluida.
    O RLS garante que só o colaborador dono da NC consegue gravar."""
    return nc_service.aceitar_nc(usuario, nc_id, dados)
