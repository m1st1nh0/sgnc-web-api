"""
Dependência de autenticação usada em (quase) toda rota da API.

Como funciona na prática:
1. O React manda o token no cabeçalho: "Authorization: Bearer <token>"
2. O FastAPI chama usuario_atual() automaticamente antes de rodar
   a função da rota (isso é o que "Depends(usuario_atual)" faz)
3. usuario_atual() valida o token com o Supabase e devolve os
   dados do usuário (id, papel, nome) já carregados da tabela
   `usuarios`

Se o token for inválido/expirado, a API responde 401 automaticamente
e a função da rota nem chega a ser executada.
"""
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.supabase_client import cliente_do_usuario

seguranca = HTTPBearer()


@dataclass
class UsuarioLogado:
    id: str
    nome: str
    email: str
    papel: str  # "adm" | "supervisor" | "funcionario"
    senha_provisoria: bool
    token: str  # guardamos o token para repassar nas próximas consultas


def usuario_atual(
    credenciais: HTTPAuthorizationCredentials = Depends(seguranca),
) -> UsuarioLogado:
    token = credenciais.credentials
    cliente = cliente_do_usuario(token)

    # Valida o token junto ao Supabase Auth e recupera o id do usuário
    resposta_auth = cliente.auth.get_user(token)
    if resposta_auth is None or resposta_auth.user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão inválida ou expirada. Faça login novamente.",
        )

    usuario_id = resposta_auth.user.id

    # Busca o papel/nome na nossa tabela usuarios (respeitando RLS:
    # a policy "usuarios_ler_proprio" garante que ele pode ler a si mesmo)
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


def exigir_adm(usuario: UsuarioLogado = Depends(usuario_atual)) -> UsuarioLogado:
    """Dependência extra para rotas que só o ADM pode chamar.

    Nota: isso é uma camada de conveniência/clareza na API. A garantia
    "de verdade" continua sendo o RLS no banco — mesmo que esta checagem
    tivesse algum bug, o Supabase rejeitaria a operação de qualquer forma.
    """
    if usuario.papel != "adm":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas o administrador (ticket manager) pode fazer isso.",
        )
    return usuario


def exigir_gestao(usuario: UsuarioLogado = Depends(usuario_atual)) -> UsuarioLogado:
    """Dependência para rotas abertas a ADM e supervisores (ex: Insights).

    Nota: assim como exigir_adm, é uma camada de clareza da API. A
    garantia "de verdade" continua sendo o RLS no banco.
    """
    if usuario.papel not in {"adm", "supervisor"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Acesso restrito a administradores e supervisores."
            ),
        )
    return usuario
