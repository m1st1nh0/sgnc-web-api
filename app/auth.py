"""Dependências de autenticação e autorização da API SGNC."""
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.supabase_client import cliente_do_usuario

seguranca = HTTPBearer()


@dataclass
class UsuarioLogado:
    id: str
    nome: str
    email: str
    papel: str  # "adm" | "supervisor" | "funcionario"
    senha_provisoria: bool
    token: str


def usuario_atual(
    credenciais: HTTPAuthorizationCredentials = Depends(seguranca),
) -> UsuarioLogado:
    """Valida o token no Supabase Auth e carrega o perfil SGNC do usuário."""
    token = credenciais.credentials
    cliente = cliente_do_usuario(token)

    resposta_auth = cliente.auth.get_user(token)
    if resposta_auth is None or resposta_auth.user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão inválida ou expirada. Faça login novamente.",
        )

    usuario_id = resposta_auth.user.id
    resultado = (
        cliente.table("usuarios")
        .select("id, nome, email, papel, ativo, senha_provisoria")
        .eq("id", usuario_id)
        .single()
        .execute()
    )

    dados = resultado.data
    if dados is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário autenticado, mas sem cadastro no sistema SGNC.",
        )

    if not dados["ativo"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário desativado. Contate o administrador.",
        )

    return UsuarioLogado(
        id=dados["id"],
        nome=dados["nome"],
        email=dados["email"],
        papel=dados["papel"],
        senha_provisoria=dados["senha_provisoria"],
        token=token,
    )


def exigir_senha_definitiva(
    usuario: UsuarioLogado = Depends(usuario_atual),
) -> UsuarioLogado:
    """Bloqueia operações de negócio até a troca da senha provisória.

    A rota de troca de senha continua dependendo diretamente de
    ``usuario_atual`` para que o primeiro acesso consiga completar o fluxo.
    """
    if usuario.senha_provisoria:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Troque a senha provisória antes de continuar.",
        )
    return usuario


def exigir_adm(
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
) -> UsuarioLogado:
    if usuario.papel != "adm":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas o administrador (Qualidade) pode fazer isso.",
        )
    return usuario


def exigir_gestao(
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
) -> UsuarioLogado:
    if usuario.papel not in {"adm", "supervisor"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito a administradores e supervisores.",
        )
    return usuario
