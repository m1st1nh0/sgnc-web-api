from fastapi import APIRouter, Depends

from app.auth import UsuarioLogado, usuario_atual, exigir_adm
from app.schemas_usuario import UsuarioEntrada, UsuarioEdicao, TrocarSenhaEntrada
from app import usuario_service
from app import nc_service


router = APIRouter(prefix="/usuarios", tags=["usuários"])


@router.get("")
def listar(usuario: UsuarioLogado = Depends(usuario_atual)):
    return usuario_service.listar_usuarios(usuario)


@router.get("/{usuario_id}/estatisticas")
def estatisticas(
    usuario_id: str,
    usuario: UsuarioLogado = Depends(usuario_atual),
):
    """
    Estatísticas de reincidência e medidas disciplinares de um colaborador.

    Regras de acesso:
    - o próprio colaborador vê as próprias estatísticas;
    - o supervisor direto vê as dos seus supervisionados;
    - o ADM vê as de qualquer colaborador.
    """
    return nc_service.obter_estatisticas_colaborador(usuario, usuario_id)


@router.post("")
def cadastrar(dados: UsuarioEntrada, usuario: UsuarioLogado = Depends(exigir_adm)):
    return usuario_service.criar_usuario(usuario, dados)



@router.put("/{usuario_id}")
def editar(usuario_id: str, dados: UsuarioEdicao, usuario: UsuarioLogado = Depends(exigir_adm)):
    """Edita nome, papel, setor e supervisor. Só ADM pode fazer isso."""
    return usuario_service.editar_usuario(usuario, usuario_id, dados)


@router.patch("/{usuario_id}/desativar")
def desativar(usuario_id: str, usuario: UsuarioLogado = Depends(exigir_adm)):
    """Desativa o usuário (ativo=false + ban no Auth). Preserva histórico."""
    return usuario_service.desativar_usuario(usuario, usuario_id)


@router.patch("/{usuario_id}/reativar")
def reativar(usuario_id: str, usuario: UsuarioLogado = Depends(exigir_adm)):
    """Reativa um usuário previamente desativado."""
    return usuario_service.reativar_usuario(usuario, usuario_id)


@router.post("/trocar-senha")
def trocar_senha(dados: TrocarSenhaEntrada, usuario: UsuarioLogado = Depends(usuario_atual)):
    usuario_service.trocar_senha(usuario, dados)
    return {"status": "senha_alterada"}
