"""
Regras de negócio de Não Conformidade.

Fluxo (v2):
    aberta --(ADM avalia)--> invalidada
                          `-> validada --(ADM envia)--> aguardando_analise
                                              --(ADM aplica feedback)--> aguardando_aceite
                                              --(colaborador aceita)--> concluida
"""

from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.auth import UsuarioLogado
from app.schemas_nc import TEXTO_ACEITE_ESPERADO
from app.supabase_client import cliente_do_usuario, cliente_servico

# =====================================================
# Causas (autocomplete que cresce com o uso)
# =====================================================


def obter_ou_criar_causas(
    cliente, nomes_causas: list[str], usuario_id: str
) -> list[int]:
    ids = []
    for nome_bruto in nomes_causas:
        nome = nome_bruto.strip()
        if not nome:
            continue

        existente = (
            cliente.table("causas").select("id").ilike("descricao", nome).execute()
        )
        if existente.data:
            ids.append(existente.data[0]["id"])
            continue

        criada = (
            cliente.table("causas")
            .insert({"descricao": nome, "criado_por": usuario_id})
            .execute()
        )
        ids.append(criada.data[0]["id"])
    return ids


def listar_causas_conhecidas(cliente) -> list[str]:
    resultado = cliente.table("causas").select("descricao").order("descricao").execute()
    return [linha["descricao"] for linha in resultado.data]


# =====================================================
# Reincidência
# =====================================================


def calcular_reincidencia(
    cliente, causa_ids: list[int], nc_id_excluir: int | None = None
) -> str:
    if not causa_ids:
        return "Não"

    resultado = (
        cliente.table("nc_causas").select("nc_id").in_("causa_id", causa_ids).execute()
    )
    nc_ids_relacionadas = {linha["nc_id"] for linha in resultado.data}
    if nc_id_excluir is not None:
        nc_ids_relacionadas.discard(nc_id_excluir)

    return "Sim" if nc_ids_relacionadas else "Não"


# =====================================================
# Montagem do objeto de saída (junta NC + causas)
# =====================================================


def _montar_saida_nc(cliente, nc: dict) -> dict:
    causas_rel = (
        cliente.table("nc_causas")
        .select("causas(descricao)")
        .eq("nc_id", nc["id"])
        .execute()
    )
    nc["causas"] = [
        linha["causas"]["descricao"] for linha in causas_rel.data if linha.get("causas")
    ]
    return nc


# =====================================================
# Abertura e edição
# =====================================================


def _buscar_colaborador(cliente, colaborador_id: str) -> dict | None:
    """Busca nome e setor do colaborador para preencher a NC
    automaticamente - essas informações não são mais digitadas
    livremente no formulário, vêm do cadastro do usuário."""
    if not colaborador_id:
        return None
    resultado = (
        cliente.table("usuarios")
        .select("nome, setor")
        .eq("id", colaborador_id)
        .execute()
    )
    return resultado.data[0] if resultado.data else None


def criar_nc(usuario: UsuarioLogado, dados) -> dict:
    """Qualquer papel pode abrir uma NC. Nasce em 'aberta', aguardando
    avaliação do ADM sobre se é procedente."""
    cliente = cliente_do_usuario(usuario.token)

    causa_ids = obter_ou_criar_causas(cliente, dados.causas, usuario.id)
    reincidencia = calcular_reincidencia(cliente, causa_ids)

    colaborador = _buscar_colaborador(cliente, dados.colaborador_id)

    payload = {
        "chamado": dados.chamado,
        "setor": colaborador["setor"] if colaborador else None,
        "colaborador": colaborador["nome"] if colaborador else None,
        "colaborador_id": dados.colaborador_id,
        "criticidade": dados.criticidade,
        "reincidencia": reincidencia,
        "status": "aberta",
        "descricao": dados.descricao,
        "aberto_por": usuario.id,
        "setor_responsavel": colaborador["setor"] if colaborador else None,
    }
    if dados.data is not None:
        payload["data"] = dados.data.isoformat()

    criada = cliente.table("nao_conformidades").insert(payload).execute()
    nc = criada.data[0]

    if causa_ids:
        vinculos = [{"nc_id": nc["id"], "causa_id": cid} for cid in causa_ids]
        cliente.table("nc_causas").insert(vinculos).execute()

    _registrar_historico(nc["id"], usuario.id, None, "aberta", "NC aberta")

    return _montar_saida_nc(cliente, nc)


def listar_ncs(usuario: UsuarioLogado) -> list[dict]:
    """RLS decide o que aparece: autor sempre vê a própria; colaborador
    e supervisor veem a partir de 'validada' em diante; ADM vê tudo."""
    cliente = cliente_do_usuario(usuario.token)
    resultado = (
        cliente.table("nao_conformidades")
        .select("*")
        .order("criado_em", desc=True)
        .execute()
    )
    return [_montar_saida_nc(cliente, nc) for nc in resultado.data]


def buscar_nc(usuario: UsuarioLogado, nc_id: int) -> dict:
    """Busca uma NC e aplica filtro de campos sensíveis dependendo de
    quem está acessando (autor, colaborador, responsável, ADM)."""
    cliente = cliente_do_usuario(usuario.token)
    resultado = cliente.table("nao_conformidades").select("*").eq("id", nc_id).execute()
    if not resultado.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="NC não encontrada.",
        )

    nc = _montar_saida_nc(cliente, resultado.data[0])

    # Relações com a NC
    eh_aberto_por = nc.get("aberto_por") == usuario.id
    eh_colaborador = nc.get("colaborador_id") == usuario.id
    eh_responsavel = nc.get("responsavel_id") == usuario.id
    eh_adm = usuario.papel == "adm"

    # Quem pode ver tudo:
    pode_ver_completo = eh_adm or eh_colaborador or eh_responsavel

    # Autor que não é ADM/colaborador/responsável vê versão podada
    if eh_aberto_por and not pode_ver_completo:
        # Campos sensíveis: feedback, motivo de invalidação, aceite e datas associadas
        nc["motivo_invalidacao"] = None
        nc["feedback"] = None
        nc["texto_aceite"] = None
        nc["validado_em"] = None
        nc["feedback_aplicado_em"] = None
        nc["aceito_em"] = None

        # Aqui você pode decidir se quer ocultar mais coisas
        # (por exemplo, evidências) dependendo do seu modelo
        # de segurança. Por enquanto mantemos descrição e causas
        # para o autor lembrar o que registrou.

    return nc


def atualizar_nc(usuario: UsuarioLogado, nc_id: int, dados) -> dict:
    """Edição livre de campos só faz sentido enquanto a NC ainda não
    foi avaliada (status 'aberta'). Depois disso, ADM usa os endpoints
    específicos (avaliar/feedback) em vez de editar tudo de novo."""
    cliente = cliente_do_usuario(usuario.token)

    causa_ids = obter_ou_criar_causas(cliente, dados.causas, usuario.id)
    reincidencia = calcular_reincidencia(cliente, causa_ids, nc_id_excluir=nc_id)

    colaborador = _buscar_colaborador(cliente, dados.colaborador_id)

    payload = {
        "chamado": dados.chamado,
        "setor": colaborador["setor"] if colaborador else None,
        "colaborador": colaborador["nome"] if colaborador else None,
        "colaborador_id": dados.colaborador_id,
        "criticidade": dados.criticidade,
        "reincidencia": reincidencia,
        "descricao": dados.descricao,
        "setor_responsavel": colaborador["setor"] if colaborador else None,
    }
    if dados.data is not None:
        payload["data"] = dados.data.isoformat()

    atualizada = (
        cliente.table("nao_conformidades").update(payload).eq("id", nc_id).execute()
    )
    if not atualizada.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="NC não encontrada.",
        )

    cliente.table("nc_causas").delete().eq("nc_id", nc_id).execute()
    if causa_ids:
        vinculos = [{"nc_id": nc_id, "causa_id": cid} for cid in causa_ids]
        cliente.table("nc_causas").insert(vinculos).execute()

    return _montar_saida_nc(cliente, atualizada.data[0])


def excluir_nc(usuario: UsuarioLogado, nc_id: int) -> None:
    cliente = cliente_do_usuario(usuario.token)
    cliente.table("nao_conformidades").delete().eq("id", nc_id).execute()


# =====================================================
# Fluxo de avaliação / envio / feedback / aceite
# =====================================================


def avaliar_nc(usuario: UsuarioLogado, nc_id: int, dados) -> dict:
    """ADM decide se a NC é procedente (aberta -> validada | invalidada)."""
    cliente = cliente_do_usuario(usuario.token)

    atual = buscar_nc(usuario, nc_id)
    if atual["status"] != "aberta":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Só é possível avaliar NCs que estão em 'aberta'.",
        )

    if dados.decisao not in ("validar", "invalidar"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="decisao deve ser 'validar' ou 'invalidar'.",
        )

    if dados.decisao == "invalidar" and not (
        dados.motivo_invalidacao and dados.motivo_invalidacao.strip()
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Informe o motivo da invalidação.",
        )

    payload = {"responsavel_id": usuario.id}

    if dados.decisao == "validar":
        payload["status"] = "validada"
        payload["validado_em"] = datetime.now(timezone.utc).isoformat()
    else:
        payload["status"] = "invalidada"
        payload["motivo_invalidacao"] = dados.motivo_invalidacao

    atualizada = (
        cliente.table("nao_conformidades").update(payload).eq("id", nc_id).execute()
    )

    _registrar_historico(
        nc_id,
        usuario.id,
        "aberta",
        payload["status"],
        dados.motivo_invalidacao,
    )

    return _montar_saida_nc(cliente, atualizada.data[0])


def enviar_nc(usuario: UsuarioLogado, nc_id: int) -> dict:
    """validada -> aguardando_analise. A partir daqui colaborador e
    supervisor passam a enxergar a NC (regra garantida pelo RLS)."""
    cliente = cliente_do_usuario(usuario.token)

    atual = buscar_nc(usuario, nc_id)
    if atual["status"] != "validada":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Só é possível enviar NCs que estão validadas.",
        )
    if not atual.get("colaborador_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Defina o colaborador responsável antes de enviar a NC.",
        )

    atualizada = (
        cliente.table("nao_conformidades")
        .update({"status": "aguardando_analise"})
        .eq("id", nc_id)
        .execute()
    )

    _registrar_historico(
        nc_id,
        usuario.id,
        "validada",
        "aguardando_analise",
        "NC enviada ao colaborador e supervisor",
    )

    return _montar_saida_nc(cliente, atualizada.data[0])


def aplicar_feedback(usuario: UsuarioLogado, nc_id: int, dados) -> dict:
    """aguardando_analise -> aguardando_aceite. O parecer/combinado
    fica visível ao colaborador e ao supervisor a partir daqui."""
    cliente = cliente_do_usuario(usuario.token)

    atual = buscar_nc(usuario, nc_id)
    if atual["status"] != "aguardando_analise":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Só é possível aplicar feedback em NCs que estão aguardando análise.",
        )

    if not dados.feedback or not dados.feedback.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Informe o feedback.",
        )

    atualizada = (
        cliente.table("nao_conformidades")
        .update(
            {
                "status": "aguardando_aceite",
                "feedback": dados.feedback,
                "feedback_aplicado_em": datetime.now(timezone.utc).isoformat(),
            }
        )
        .eq("id", nc_id)
        .execute()
    )

    _registrar_historico(
        nc_id,
        usuario.id,
        "aguardando_analise",
        "aguardando_aceite",
        "Feedback aplicado",
    )

    return _montar_saida_nc(cliente, atualizada.data[0])


def aceitar_nc(usuario: UsuarioLogado, nc_id: int, dados) -> dict:
    """Aceite formal do colaborador. Exige digitar a frase de
    confirmação exata (ignorando maiúsculas e espaços nas pontas) -
    funciona como uma 'assinatura' de baixa fricção mas intencional."""
    cliente = cliente_do_usuario(usuario.token)

    texto_normalizado = " ".join(dados.texto_aceite.strip().lower().split())
    if texto_normalizado != TEXTO_ACEITE_ESPERADO:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Para confirmar, digite exatamente: "{TEXTO_ACEITE_ESPERADO}"',
        )

    payload = {
        "status": "concluida",
        "texto_aceite": dados.texto_aceite,
        "aceito_em": datetime.now(timezone.utc).isoformat(),
    }

    atualizada = (
        cliente.table("nao_conformidades").update(payload).eq("id", nc_id).execute()
    )
    if not atualizada.data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Não foi possível registrar o aceite "
                "(a NC pode não ser sua ou não estar aguardando aceite)."
            ),
        )

    _registrar_historico(
        nc_id,
        usuario.id,
        "aguardando_aceite",
        "concluida",
        "Aceite formal do colaborador",
    )

    return _montar_saida_nc(cliente, atualizada.data[0])


def _registrar_historico(
    nc_id: int,
    usuario_id: str,
    status_anterior: str | None,
    status_novo: str,
    observacao: str | None,
):
    cliente_servico().table("historico_nc").insert(
        {
            "nc_id": nc_id,
            "usuario_id": usuario_id,
            "status_anterior": status_anterior,
            "status_novo": status_novo,
            "observacao": observacao,
        }
    ).execute()
