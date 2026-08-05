"""
Este módulo é o equivalente ao antigo banco.py, mas para Supabase
em vez de Excel.

Conceito importante: usamos DOIS tipos de cliente Supabase aqui.

1. cliente_do_usuario(token) -> um cliente "autenticado como aquele
   usuário". Toda consulta feita com ele passa pelas políticas RLS
   que criamos (um funcionário só enxerga suas próprias NCs, etc).
   É o que usamos para quase tudo.

2. cliente_servico -> um cliente com a chave "service_role", que
   ignora RLS. Usamos isso SÓ para operações internas de confiança
   da própria API, como gravar uma linha no histórico. Nunca
   repassamos essa chave para o navegador do usuário.
"""
from supabase import create_client, Client

from app.config import SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY


def cliente_do_usuario(token_acesso: str) -> Client:
    """Cria um cliente Supabase que atua "como" o usuário dono do token.

    token_acesso: o JWT (access_token) devolvido pelo login do Supabase Auth.
    """
    cliente = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    # Isto faz com que toda query subsequente use o token do usuário,
    # e portanto respeite as políticas RLS daquele papel/id.
    cliente.postgrest.auth(token_acesso)
    return cliente


_cliente_servico: Client | None = None


def cliente_servico() -> Client:
    """Cliente com privilégio total (ignora RLS). Uso interno restrito."""
    global _cliente_servico
    if _cliente_servico is None:
        _cliente_servico = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _cliente_servico
