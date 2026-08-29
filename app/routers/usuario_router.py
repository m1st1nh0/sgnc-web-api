from fastapi import APIRouter, Depends

from app.auth import (
    UsuarioLogado,
    exigir_adm,
    exigir_senha_definitiva,
    usuario_atual,
)
from app.schemas_usuario import TrocarSenhaEntrada, UsuarioEdicao, UsuarioEntrada
from app import estatisticas_service_v2
from app import usuario_service

router = APIRouter(prefix="/usuarios", tags=["usuários"])


@router.get("")
def listar(
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
):
    """ADM vê todos; supervisor só a equipe direta; funcionário só a si."""
    return usuario_service.listar_usuarios(usuario)


@router.get("/opcoes-nc")
def opcoes_nc(
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
):
    """Diretório mínimo global de colaboradores ativos para abertura de NC."""
    return usuario_service.listar_opcoes_nc(usuario)


@router.get("/{usuario_id}/estatisticas")
def estatisticas(
    usuario_id: str,
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
):
    """Próprio usuário, supervisor direto ou ADM, conforme a hierarquia."""
    return estatisticas_service_v2.obter_estatisticas_colaborador(
        usuario, usuario_id
    )


@router.post("")
def cadastrar(
    dados: UsuarioEntrada,
    usuario: UsuarioLogado = Depends(exigir_adm),
):
    return usuario_service.criar_usuario(usuario, dados)


@router.put("/{usuario_id}")
def editar(
    usuario_id: str,
    dados: UsuarioEdicao,
    usuario: UsuarioLogado = Depends(exigir_adm),
):
    return usuario_service.editar_usuario(usuario, usuario_id, dados)


@router.patch("/{usuario_id}/desativar")
def desativar(
    usuario_id: str,
    usuario: UsuarioLogado = Depends(exigir_adm),
):
    return usuario_service.desativar_usuario(usuario, usuario_id)


@router.patch("/{usuario_id}/reativar")
def reativar(
    usuario_id: str,
    usuario: UsuarioLogado = Depends(exigir_adm),
):
    return usuario_service.reativar_usuario(usuario, usuario_id)


@router.post("/trocar-senha")
def trocar_senha(
    dados: TrocarSenhaEntrada,
    usuario: UsuarioLogado = Depends(usuario_atual),
):
    """Permanece acessível durante o primeiro acesso com senha provisória."""
    usuario_service.trocar_senha(usuario, dados)
    return {"status": "senha_alterada"}
