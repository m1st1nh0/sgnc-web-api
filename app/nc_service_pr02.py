"""Fluxo de NC da PR02 com reincidência canônica e validação atômica.

O restante do workflow continua no serviço PR01. Neste módulo substituímos
somente os pontos em que a regra de reincidência participa do domínio:
abertura/edição de NC e validação procedente.
"""
from fastapi import HTTPException, status

from app.auth import UsuarioLogado
from app import nc_service as legacy
from app import nc_service_v2 as base
from app.recurrence_v2 import STATUS_QUE_CONTAM_REINCIDENCIA


# Operações que não mudam no PR02.
listar_ncs = base.listar_ncs
buscar_nc = base.buscar_nc
listar_causas_conhecidas = base.listar_causas_conhecidas
excluir_nc = base.excluir_nc
enviar_nc = base.enviar_nc
aplicar_feedback = base.aplicar_feedback
aceitar_nc = base.aceitar_nc
registrar_medida_disciplinar = base.registrar_medida_disciplinar
decidir_medida_disciplina = base.decidir_medida_disciplina


def criar_nc(usuario: UsuarioLogado, dados) -> dict:
    """Abre NC sem antecipar reincidência antes de ela ser procedente."""
    servico = base._servico()
    causa_ids = legacy.obter_ou_criar_causas(servico, dados.causas, usuario.id)
    colaborador = legacy._buscar_colaborador(servico, dados.colaborador_id)

    if dados.colaborador_id and not colaborador:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Colaborador informado não existe.",
        )

    payload = {
        "chamado": dados.chamado,
        "setor": colaborador["setor"] if colaborador else None,
        "colaborador": colaborador["nome"] if colaborador else None,
        "colaborador_id": dados.colaborador_id,
        "criticidade": dados.criticidade,
        # Projeção legada: só passa a ter significado após a validação.
        "reincidencia": "Não",
        "status": "aberta",
        "descricao": dados.descricao,
        "aberto_por": usuario.id,
        "setor_responsavel": colaborador["setor"] if colaborador else None,
    }
    if dados.data is not None:
        payload["data"] = dados.data.isoformat()

    criada = servico.table("nao_conformidades").insert(payload).execute()
    if not criada.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Não foi possível abrir a não conformidade.",
        )
    nc = criada.data[0]

    if causa_ids:
        servico.table("nc_causas").insert(
            [{"nc_id": nc["id"], "causa_id": causa_id} for causa_id in causa_ids]
        ).execute()

    legacy._registrar_historico(
        nc["id"], usuario.id, None, "aberta", "NC aberta"
    )
    return legacy._montar_saida_nc(servico, nc)


def atualizar_nc(usuario: UsuarioLogado, nc_id: int, dados) -> dict:
    """Edita NC aberta e mantém ocorrência indefinida até a validação."""
    base._exigir_adm(usuario)
    atual = base._buscar_nc_servico(nc_id)
    if atual["status"] != "aberta":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Só é possível editar os dados da NC enquanto ela está aberta.",
        )

    servico = base._servico()
    causa_ids = legacy.obter_ou_criar_causas(servico, dados.causas, usuario.id)
    colaborador = legacy._buscar_colaborador(servico, dados.colaborador_id)

    if dados.colaborador_id and not colaborador:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Colaborador informado não existe.",
        )

    payload = {
        "chamado": dados.chamado,
        "setor": colaborador["setor"] if colaborador else None,
        "colaborador": colaborador["nome"] if colaborador else None,
        "colaborador_id": dados.colaborador_id,
        "criticidade": dados.criticidade,
        "reincidencia": "Não",
        "descricao": dados.descricao,
        "setor_responsavel": colaborador["setor"] if colaborador else None,
    }
    if dados.data is not None:
        payload["data"] = dados.data.isoformat()

    atualizada = (
        servico.table("nao_conformidades")
        .update(payload)
        .eq("id", nc_id)
        .eq("status", "aberta")
        .execute()
    )
    if not atualizada.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A NC foi alterada por outro processo. Atualize a página e tente novamente.",
        )

    servico.table("nc_causas").delete().eq("nc_id", nc_id).execute()
    if causa_ids:
        servico.table("nc_causas").insert(
            [{"nc_id": nc_id, "causa_id": causa_id} for causa_id in causa_ids]
        ).execute()

    return legacy._montar_saida_nc(servico, atualizada.data[0])


def _normalizar_retorno_rpc(dados) -> dict:
    """Normaliza diferenças de serialização do PostgREST para retorno jsonb."""
    if isinstance(dados, dict):
        return dados
    if isinstance(dados, list) and len(dados) == 1 and isinstance(dados[0], dict):
        return dados[0]
    return {}


def _tratar_erro_validacao_rpc(resultado: dict) -> None:
    erro = resultado.get("erro")
    if erro == "nc_nao_encontrada":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="NC não encontrada.",
        )
    if erro == "colaborador_ausente":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Defina o colaborador analisado antes de validar a NC.",
        )
    if erro == "nc_nao_aberta":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A NC já foi avaliada por outro processo.",
        )
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Não foi possível validar a NC de forma atômica.",
    )


def avaliar_nc(usuario: UsuarioLogado, nc_id: int, dados) -> dict:
    """Avalia NC; validação procedente é atômica com a numeração das causas."""
    base._exigir_adm(usuario)
    atual = base._buscar_nc_servico(nc_id)

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

    # Invalidação não cria ocorrência e permanece no fluxo PR01.
    if dados.decisao == "invalidar":
        return base.avaliar_nc(usuario, nc_id, dados)

    if not atual.get("colaborador_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Defina o colaborador analisado antes de validar a NC.",
        )

    servico = base._servico()
    chamada = servico.rpc(
        "validar_nc_com_ocorrencias_v2",
        {"p_nc_id": nc_id, "p_responsavel_id": usuario.id},
    ).execute()
    resultado = _normalizar_retorno_rpc(chamada.data)
    if not resultado.get("ok"):
        _tratar_erro_validacao_rpc(resultado)

    ocorrencias = resultado.get("ocorrencias") or []
    for ocorrencia in ocorrencias:
        ocorrencia["medida_sugerida"] = legacy.decidir_medida_disciplina(
            ocorrencia["ocorrencia_numero"]
        )

    # A atomicidade do histórico completo entra no PR03. Aqui a transição e
    # os snapshots de ocorrência já são indivisíveis entre si.
    legacy._registrar_historico(
        nc_id,
        usuario.id,
        "aberta",
        "aguardando_feedback",
        "NC validada e disponibilizada para feedback",
    )

    atualizada = base._buscar_nc_servico(nc_id)
    resposta = legacy._montar_saida_nc(servico, atualizada)
    resposta["ocorrencias"] = ocorrencias
    return resposta
