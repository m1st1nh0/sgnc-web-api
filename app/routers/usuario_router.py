from fastapi import APIRouter, Depends

from app.auth import UsuarioLogado, usuario_atual, exigir_adm
from app.schemas_usuario import UsuarioEntrada, TrocarSenhaEntrada
from app import usuario_service

router = APIRouter(prefix="/usuarios", tags=["usuários"])


@router.get("")
def listar(usuario: UsuarioLogado = Depends(usuario_atual)):
    """Lista usuários visíveis para quem está logado (RLS decide:
    ADM vê todos, supervisor vê equipe, funcionário vê só a si mesmo).
    Útil no frontend para o ADM escolher o colaborador_id de uma NC
    por nome, em vez de precisar do UUID."""
    return usuario_service.listar_usuarios(usuario)


@router.post("")
def cadastrar(dados: UsuarioEntrada, usuario: UsuarioLogado = Depends(exigir_adm)):
    """Só o ADM cadastra novos usuários. Cria o login (Auth) e o
    registro do sistema (tabela usuarios) numa única chamada.
    A senha informada aqui é provisória: o usuário é obrigado a
    trocá-la no primeiro acesso via POST /auth/trocar-senha."""
    return usuario_service.criar_usuario(usuario, dados)


@router.post("/trocar-senha")
def trocar_senha(dados: TrocarSenhaEntrada, usuario: UsuarioLogado = Depends(usuario_atual)):
    """Qualquer usuário logado troca a própria senha.
    Usado tanto na primeira troca obrigatória quanto para trocas
    espontâneas depois."""
    usuario_service.trocar_senha(usuario, dados)
    return {"status": "senha_alterada"}
