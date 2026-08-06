from fastapi import APIRouter, HTTPException, status

from app.schemas_auth import LoginEntrada, LoginSaida
from app.supabase_client import cliente_do_usuario
from app.config import SUPABASE_URL, SUPABASE_ANON_KEY
from supabase import create_client

router = APIRouter(prefix="/auth", tags=["autenticação"])


@router.post("/login", response_model=LoginSaida)
def login(dados: LoginEntrada):
    """Autentica no Supabase Auth e devolve o token que o React deve
    guardar e enviar em toda requisição seguinte
    (cabeçalho Authorization: Bearer <token>)."""

    # Para o login em si ainda não temos um token, então usamos um
    # cliente "anônimo" temporário só para chamar sign_in.
    cliente_anonimo = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

    try:
        resposta = cliente_anonimo.auth.sign_in_with_password(
            {"email": dados.email, "password": dados.senha}
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou senha inválidos.",
        )

    if resposta.session is None or resposta.user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou senha inválidos.",
        )

    token = resposta.session.access_token
    usuario_id = resposta.user.id

    # Agora sim, com o token em mãos, buscamos o papel/nome respeitando RLS
    cliente = cliente_do_usuario(token)
    resultado = (
        cliente.table("usuarios")
        .select("id, nome, email, papel, ativo, senha_provisoria")
        .eq("id", usuario_id)
        .single()
        .execute()
    )

    dados_usuario = resultado.data
    if dados_usuario is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Login válido, mas sem cadastro no sistema SGNC. Contate o administrador.",
        )

    if not dados_usuario["ativo"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário desativado. Contate o administrador.",
        )

    return LoginSaida(
        token=token,
        usuario_id=dados_usuario["id"],
        nome=dados_usuario["nome"],
        email=dados_usuario["email"],
        papel=dados_usuario["papel"],
        senha_provisoria=dados_usuario["senha_provisoria"],
    )
