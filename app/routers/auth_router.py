from fastapi import APIRouter, HTTPException, Request, status

from app.schemas_auth import LoginEntrada, LoginSaida
from app.supabase_client import cliente_do_usuario
from app.config import SUPABASE_URL, SUPABASE_ANON_KEY
from app.security_hardening import LOGIN_RATE_LIMITER
from supabase import create_client

router = APIRouter(prefix="/auth", tags=["autenticação"])


@router.post("/login", response_model=LoginSaida)
def login(dados: LoginEntrada, request: Request):
    """Autentica no Supabase Auth e devolve o token da sessão.

    O throttle local é defesa em profundidade aos limites do Supabase Auth e
    considera a combinação normalizada de email + peer da conexão, armazenada
    somente como hash em memória.
    """
    peer = request.client.host if request.client else None
    chave_rate_limit = LOGIN_RATE_LIMITER.chave(str(dados.email), peer)
    LOGIN_RATE_LIMITER.verificar(chave_rate_limit)

    cliente_anonimo = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

    try:
        resposta = cliente_anonimo.auth.sign_in_with_password(
            {"email": dados.email, "password": dados.senha}
        )
    except Exception:
        LOGIN_RATE_LIMITER.registrar_falha(chave_rate_limit)
        LOGIN_RATE_LIMITER.verificar(chave_rate_limit)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou senha inválidos.",
        )

    if resposta.session is None or resposta.user is None:
        LOGIN_RATE_LIMITER.registrar_falha(chave_rate_limit)
        LOGIN_RATE_LIMITER.verificar(chave_rate_limit)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou senha inválidos.",
        )

    # Credencial correta: não mantemos penalidade de tentativas anteriores.
    LOGIN_RATE_LIMITER.registrar_sucesso(chave_rate_limit)

    token = resposta.session.access_token
    usuario_id = resposta.user.id

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
