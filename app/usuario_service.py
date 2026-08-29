"""Cadastro, gestão e diretório de usuários do SGNC."""
from fastapi import HTTPException, status

from app.auth import UsuarioLogado
from app.supabase_client import cliente_do_usuario, cliente_servico

PAPEIS_VALIDOS = {"adm", "supervisor", "funcionario"}


def criar_usuario(usuario_logado: UsuarioLogado, dados) -> dict:
    if dados.papel not in PAPEIS_VALIDOS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="papel inválido.")

    if dados.papel != "adm" and not dados.supervisor_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="supervisor_id é obrigatório para papel 'funcionario' ou 'supervisor'.",
        )

    if len(dados.senha_inicial) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Senha inicial deve ter ao menos 6 caracteres.",
        )

    servico = cliente_servico()

    try:
        resposta_auth = servico.auth.admin.create_user(
            {
                "email": dados.email,
                "password": dados.senha_inicial,
                "email_confirm": True,
            }
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não foi possível criar o login. Verifique os dados informados.",
        )

    novo_id = resposta_auth.user.id

    try:
        criado = (
            servico.table("usuarios")
            .insert(
                {
                    "id": novo_id,
                    "nome": dados.nome,
                    "email": dados.email,
                    "papel": dados.papel,
                    "setor": dados.setor,
                    "supervisor_id": dados.supervisor_id,
                    "senha_provisoria": True,
                }
            )
            .execute()
        )
    except Exception:
        servico.auth.admin.delete_user(novo_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não foi possível cadastrar o usuário.",
        )

    return criado.data[0]


def _consulta_usuarios_por_escopo(usuario_logado: UsuarioLogado, selecao: str):
    """Retorna uma consulta de usuários limitada ao papel do solicitante.

    - ADM: organização inteira;
    - Supervisor: somente subordinados diretos;
    - Funcionário: somente o próprio cadastro.

    Esse escopo vale para telas administrativas e estatísticas. O diretório
    mínimo usado na abertura de NC é intencionalmente global, pois qualquer
    usuário autenticado pode abrir uma NC sobre qualquer colaborador ativo.
    """
    consulta = cliente_servico().table("usuarios").select(selecao)
    if usuario_logado.papel == "supervisor":
        consulta = consulta.eq("supervisor_id", usuario_logado.id)
    elif usuario_logado.papel != "adm":
        consulta = consulta.eq("id", usuario_logado.id)
    return consulta


def listar_usuarios(usuario_logado: UsuarioLogado) -> list[dict]:
    resultado = (
        _consulta_usuarios_por_escopo(
            usuario_logado,
            "id, nome, email, papel, setor, supervisor_id, ativo, senha_provisoria",
        )
        .order("nome")
        .execute()
    )
    return resultado.data


def listar_opcoes_nc(usuario_logado: UsuarioLogado) -> list[dict]:
    """Diretório mínimo global para selecionar o colaborador de uma NC.

    Qualquer usuário autenticado pode abrir uma NC sobre qualquer colaborador
    ativo. Para preservar minimização de dados, este endpoint usa service_role
    no servidor e retorna somente ``id``, ``nome`` e ``setor``. A listagem
    administrativa de usuários continua obedecendo ao escopo por papel.
    """
    resultado = (
        cliente_servico()
        .table("usuarios")
        .select("id, nome, setor, ativo")
        .eq("ativo", True)
        .order("nome")
        .execute()
    )
    return [
        {"id": u["id"], "nome": u["nome"], "setor": u.get("setor")}
        for u in resultado.data
    ]


def editar_usuario(usuario_logado: UsuarioLogado, usuario_id: str, dados) -> dict:
    if dados.papel not in PAPEIS_VALIDOS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="papel inválido.")

    if dados.papel != "adm" and not dados.supervisor_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="supervisor_id é obrigatório para papel 'funcionario' ou 'supervisor'.",
        )

    servico = cliente_servico()
    resultado = (
        servico.table("usuarios")
        .update(
            {
                "nome": dados.nome,
                "papel": dados.papel,
                "setor": dados.setor,
                "supervisor_id": dados.supervisor_id if dados.papel != "adm" else None,
            }
        )
        .eq("id", usuario_id)
        .execute()
    )

    if not resultado.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não encontrado.",
        )

    return resultado.data[0]


def desativar_usuario(usuario_logado: UsuarioLogado, usuario_id: str) -> dict:
    if usuario_id == usuario_logado.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Você não pode desativar sua própria conta.",
        )

    servico = cliente_servico()
    servico.auth.admin.update_user_by_id(
        usuario_id, {"ban_duration": "876000h"}
    )
    resultado = (
        servico.table("usuarios")
        .update({"ativo": False})
        .eq("id", usuario_id)
        .execute()
    )

    if not resultado.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não encontrado.",
        )
    return resultado.data[0]


def reativar_usuario(usuario_logado: UsuarioLogado, usuario_id: str) -> dict:
    servico = cliente_servico()
    servico.auth.admin.update_user_by_id(usuario_id, {"ban_duration": "none"})
    resultado = (
        servico.table("usuarios")
        .update({"ativo": True})
        .eq("id", usuario_id)
        .execute()
    )

    if not resultado.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não encontrado.",
        )
    return resultado.data[0]


def trocar_senha(usuario_logado: UsuarioLogado, dados) -> None:
    cliente = cliente_do_usuario(usuario_logado.token)

    if len(dados.senha_nova) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nova senha deve ter ao menos 6 caracteres.",
        )

    try:
        cliente.auth.sign_in_with_password(
            {"email": usuario_logado.email, "password": dados.senha_atual}
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Senha atual incorreta.",
        )

    try:
        cliente.auth.update_user({"password": dados.senha_nova})
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não foi possível trocar a senha.",
        )

    cliente_servico().table("usuarios").update(
        {"senha_provisoria": False}
    ).eq("id", usuario_logado.id).execute()
