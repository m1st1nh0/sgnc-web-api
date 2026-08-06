"""
Cadastro e gestão de usuários do sistema.

Criar um usuário é uma operação em duas etapas encadeadas:
1. Criar o login no Supabase Auth (isso exige a chave service_role,
   porque é uma ação "administrativa" que nenhum usuário comum,
   nem mesmo o ADM com seu próprio token, tem permissão de fazer)
2. Inserir o registro correspondente na tabela `usuarios`, com o
   MESMO id gerado no passo 1

Se o passo 2 falhar depois do passo 1 ter dado certo, ficaríamos com
um login "órfão" no Auth sem registro no sistema. Por isso, se o
passo 2 falhar, desfazemos o passo 1 (apagamos o usuário do Auth).
"""
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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Senha inicial deve ter ao menos 6 caracteres.")

    servico = cliente_servico()

    # Etapa 1: cria o login no Supabase Auth
    try:
        resposta_auth = servico.auth.admin.create_user({
            "email": dados.email,
            "password": dados.senha_inicial,
            "email_confirm": True,  # já nasce confirmado, sem precisar clicar em link de email
        })
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Não foi possível criar o login: {e}",
        )

    novo_id = resposta_auth.user.id

    # Etapa 2: insere o registro na tabela usuarios
    try:
        criado = (
            servico.table("usuarios")
            .insert({
                "id": novo_id,
                "nome": dados.nome,
                "email": dados.email,
                "papel": dados.papel,
                "setor": dados.setor,
                "supervisor_id": dados.supervisor_id,
                "senha_provisoria": True,
            })
            .execute()
        )
    except Exception as e:
        # Desfaz o login criado no Auth, para não deixar "usuário fantasma"
        servico.auth.admin.delete_user(novo_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Não foi possível cadastrar o usuário: {e}",
        )

    return criado.data[0]


def listar_usuarios(usuario_logado: UsuarioLogado) -> list[dict]:
    """RLS decide o que aparece: ADM vê todos; supervisor vê a si
    mesmo e seus subordinados diretos; funcionário vê só a si mesmo."""
    cliente = cliente_do_usuario(usuario_logado.token)
    resultado = (
        cliente.table("usuarios")
        .select("id, nome, email, papel, setor, supervisor_id, ativo, senha_provisoria")
        .order("nome")
        .execute()
    )
    return resultado.data


def trocar_senha(usuario_logado: UsuarioLogado, dados) -> None:
    cliente = cliente_do_usuario(usuario_logado.token)

    if len(dados.senha_nova) < 6:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nova senha deve ter ao menos 6 caracteres.")

    # Confirma a senha atual tentando logar com ela (forma simples de
    # validar sem duplicar lógica de hash/verificação por conta própria)
    try:
        cliente.auth.sign_in_with_password({
            "email": usuario_logado.email,
            "password": dados.senha_atual,
        })
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Senha atual incorreta.")

    try:
        cliente.auth.update_user({"password": dados.senha_nova})
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Não foi possível trocar a senha: {e}")

    # Usa o cliente de serviço para limpar a flag (o token antigo do
    # usuário pode ter ficado inválido após a troca de senha)
    cliente_servico().table("usuarios").update({"senha_provisoria": False}).eq(
        "id", usuario_logado.id
    ).execute()
