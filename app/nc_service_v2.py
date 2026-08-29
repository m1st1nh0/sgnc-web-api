"""Fluxo V2 de Não Conformidades.

Leituras continuam usando o token do usuário e o RLS. Mutações de negócio
são autorizadas pela API e gravadas com o cliente de serviço, evitando que
as permissões de escrita da Data API precisem ficar expostas ao navegador.

Compatibilidade de rollout:
- ``validada`` e ``aguardando_analise`` continuam reconhecidos como legados;
- novas validações entram diretamente em ``aguardando_feedback``;
- ``enviar_nc`` existe apenas para avançar registros legados em ``validada``.
"""
from datetime import date, datetime, timezone

from fastapi import HTTPException, status

from app.auth import UsuarioLogado
from app.schemas_nc import TEXTO_ACEITE_ESPERADO
from app.supabase_client import cliente_servico
from app import nc_service as legacy


STATUS_QUE_CONTAM_REINCIDENCIA = [
    "validada",  # legado
    "aguardando_analise",  # legado
    "aguardando_feedback",
    "aguardando_aceite",
    "concluida",
]

STATUS_VISIVEIS_APOS_VALIDACAO = {
    "aguardando_feedback",
    "aguardando_analise",  # legado
    "aguardando_aceite",
    "concluida",
}


# Leituras permanecem protegidas pelo RLS do usuário.
listar_ncs = legacy.listar_ncs
buscar_nc = legacy.buscar_nc
listar_causas_conhecidas = legacy.listar_causas_conhecidas
decidir_medida_disciplina = legacy.decidir_medida_disciplina
obter_estatisticas_colaborador = legacy.obter_estatisticas_colaborador


def _agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _servico():
    return cliente_servico()


def _exigir_adm(usuario: UsuarioLogado) -> None:
    if usuario.papel != "adm":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas o administrador (Qualidade) pode fazer isso.",
        )


def _buscar_nc_servico(nc_id: int) -> dict:
    resultado = (
        _servico()
        .table("nao_conformidades")
        .select("*")
        .eq("id", nc_id)
        .execute()
    )
    if not resultado.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="NC não encontrada.",
        )
    return resultado.data[0]


def _montar_saida_servico(nc: dict) -> dict:
    return legacy._montar_saida_nc(_servico(), nc)


def _obter_ou_criar_causas_servico(
    nomes_causas: list[str], usuario_id: str
) -> list[int]:
    return legacy.obter_ou_criar_causas(
        _servico(), nomes_causas, usuario_id
    )


def criar_nc(usuario: UsuarioLogado, dados) -> dict:
    """Cria uma NC em ``aberta`` com campos sensíveis definidos no servidor."""
    servico = _servico()
    causa_ids = legacy.obter_ou_criar_causas(
        servico, dados.causas, usuario.id
    )
    reincidencia = legacy.calcular_reincidencia(servico, causa_ids)
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
        "reincidencia": reincidencia,
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
            [{"nc_id": nc["id"], "causa_id": cid} for cid in causa_ids]
        ).execute()

    legacy._registrar_historico(
        nc["id"], usuario.id, None, "aberta", "NC aberta"
    )
    return legacy._montar_saida_nc(servico, nc)


def atualizar_nc(usuario: UsuarioLogado, nc_id: int, dados) -> dict:
    """ADM edita os dados-base somente enquanto a NC estiver aberta."""
    _exigir_adm(usuario)
    atual = _buscar_nc_servico(nc_id)
    if atual["status"] != "aberta":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Só é possível editar os dados da NC enquanto ela está aberta.",
        )

    servico = _servico()
    causa_ids = legacy.obter_ou_criar_causas(
        servico, dados.causas, usuario.id
    )
    reincidencia = legacy.calcular_reincidencia(
        servico, causa_ids, nc_id_excluir=nc_id
    )
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
        "reincidencia": reincidencia,
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
            [{"nc_id": nc_id, "causa_id": cid} for cid in causa_ids]
        ).execute()

    return legacy._montar_saida_nc(servico, atualizada.data[0])


def excluir_nc(usuario: UsuarioLogado, nc_id: int) -> None:
    _exigir_adm(usuario)
    _buscar_nc_servico(nc_id)
    _servico().table("nao_conformidades").delete().eq("id", nc_id).execute()


def avaliar_nc(usuario: UsuarioLogado, nc_id: int, dados) -> dict:
    """ADM avalia a NC.

    Novo fluxo:
        aberta -> aguardando_feedback
        aberta -> invalidada

    A validação já torna a NC disponível ao colaborador e ao supervisor
    direto, eliminando a etapa separada de envio.
    """
    _exigir_adm(usuario)
    atual = _buscar_nc_servico(nc_id)

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

    servico = _servico()

    if dados.decisao == "invalidar":
        motivo = (dados.motivo_invalidacao or "").strip()
        if not motivo:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Informe o motivo da invalidação.",
            )

        atualizada = (
            servico.table("nao_conformidades")
            .update(
                {
                    "responsavel_id": usuario.id,
                    "status": "invalidada",
                    "motivo_invalidacao": motivo,
                    "decidido_em": _agora_iso(),
                }
            )
            .eq("id", nc_id)
            .eq("status", "aberta")
            .execute()
        )
        if not atualizada.data:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A NC já foi avaliada por outro processo.",
            )

        legacy._registrar_historico(
            nc_id, usuario.id, "aberta", "invalidada", motivo
        )
        return legacy._montar_saida_nc(servico, atualizada.data[0])

    colaborador_id = atual.get("colaborador_id")
    if not colaborador_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Defina o colaborador analisado antes de validar a NC.",
        )

    data_referencia = date.today()
    if atual.get("data"):
        try:
            data_referencia = date.fromisoformat(str(atual["data"]))
        except ValueError:
            pass

    ocorrencias = legacy.calcular_ocorrencias_da_nc(
        cliente=servico,
        nc_id=nc_id,
        colaborador_id=colaborador_id,
        data_referencia=data_referencia,
    )
    for ocorrencia in ocorrencias:
        ocorrencia["medida_sugerida"] = legacy.decidir_medida_disciplina(
            ocorrencia["ocorrencia_numero"]
        )

    agora = _agora_iso()
    atualizada = (
        servico.table("nao_conformidades")
        .update(
            {
                "responsavel_id": usuario.id,
                "status": "aguardando_feedback",
                "validado_em": agora,
                "enviado_em": agora,
                "decidido_em": agora,
            }
        )
        .eq("id", nc_id)
        .eq("status", "aberta")
        .execute()
    )
    if not atualizada.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A NC já foi avaliada por outro processo.",
        )

    for ocorrencia in ocorrencias:
        (
            servico.table("nc_causas")
            .update({"ocorrencia_numero": ocorrencia["ocorrencia_numero"]})
            .eq("nc_id", nc_id)
            .eq("causa_id", ocorrencia["causa_id"])
            .execute()
        )

    legacy._registrar_historico(
        nc_id,
        usuario.id,
        "aberta",
        "aguardando_feedback",
        "NC validada e disponibilizada para feedback",
    )

    resposta = legacy._montar_saida_nc(servico, atualizada.data[0])
    resposta["ocorrencias"] = ocorrencias
    return resposta


def enviar_nc(usuario: UsuarioLogado, nc_id: int) -> dict:
    """Compatibilidade temporária para NCs legadas em ``validada``."""
    _exigir_adm(usuario)
    atual = _buscar_nc_servico(nc_id)
    if atual["status"] != "validada":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Esta rota é exclusiva para NCs legadas em 'validada'.",
        )

    agora = _agora_iso()
    atualizada = (
        _servico().table("nao_conformidades")
        .update({"status": "aguardando_feedback", "enviado_em": agora})
        .eq("id", nc_id)
        .eq("status", "validada")
        .execute()
    )
    if not atualizada.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A NC já foi alterada por outro processo.",
        )

    legacy._registrar_historico(
        nc_id,
        usuario.id,
        "validada",
        "aguardando_feedback",
        "NC legada avançada para o novo fluxo de feedback",
    )
    return _montar_saida_servico(atualizada.data[0])


def aplicar_feedback(usuario: UsuarioLogado, nc_id: int, dados) -> dict:
    _exigir_adm(usuario)
    atual = _buscar_nc_servico(nc_id)
    status_atual = atual["status"]
    if status_atual not in {"aguardando_feedback", "aguardando_analise"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Só é possível aplicar feedback em NCs que estão aguardando feedback.",
        )

    feedback = (dados.feedback or "").strip()
    if not feedback:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Informe o feedback.",
        )

    servico = _servico()
    atualizada = (
        servico.table("nao_conformidades")
        .update(
            {
                "status": "aguardando_aceite",
                "feedback": feedback,
                "feedback_aplicado_em": _agora_iso(),
            }
        )
        .eq("id", nc_id)
        .eq("status", status_atual)
        .execute()
    )
    if not atualizada.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A NC já foi alterada por outro processo.",
        )

    legacy._registrar_historico(
        nc_id,
        usuario.id,
        status_atual,
        "aguardando_aceite",
        "Feedback aplicado",
    )
    return legacy._montar_saida_nc(servico, atualizada.data[0])


def aceitar_nc(usuario: UsuarioLogado, nc_id: int, dados) -> dict:
    """Somente o colaborador alvo pode concluir uma NC aguardando aceite."""
    texto_normalizado = " ".join(dados.texto_aceite.strip().lower().split())
    if texto_normalizado != TEXTO_ACEITE_ESPERADO:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Para confirmar, digite exatamente: "{TEXTO_ACEITE_ESPERADO}"',
        )

    # A leitura com token/RLS confirma primeiro que a linha é visível.
    atual = legacy.buscar_nc(usuario, nc_id)
    if atual.get("colaborador_id") != usuario.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Somente o colaborador analisado pode registrar o aceite.",
        )
    if atual.get("status") != "aguardando_aceite":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A NC não está aguardando aceite.",
        )

    servico = _servico()
    atualizada = (
        servico.table("nao_conformidades")
        .update(
            {
                "status": "concluida",
                "texto_aceite": dados.texto_aceite,
                "aceito_em": _agora_iso(),
            }
        )
        .eq("id", nc_id)
        .eq("colaborador_id", usuario.id)
        .eq("status", "aguardando_aceite")
        .execute()
    )
    if not atualizada.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A NC já foi alterada por outro processo.",
        )

    legacy._registrar_historico(
        nc_id,
        usuario.id,
        "aguardando_aceite",
        "concluida",
        "Aceite formal do colaborador",
    )
    return legacy._montar_saida_nc(servico, atualizada.data[0])


def registrar_medida_disciplinar(usuario: UsuarioLogado, dados) -> dict:
    """Registra medida disciplinar manualmente, sempre pelo backend."""
    _exigir_adm(usuario)
    servico = _servico()

    resultado_nc = (
        servico.table("nao_conformidades")
        .select("id, colaborador_id")
        .eq("id", dados.nc_id)
        .execute()
    )
    if not resultado_nc.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Não conformidade não encontrada.",
        )
    nc = resultado_nc.data[0]
    if not nc.get("colaborador_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A não conformidade não possui um colaborador associado.",
        )

    resultado_causa = (
        servico.table("nc_causas")
        .select("causa_id, ocorrencia_numero")
        .eq("nc_id", dados.nc_id)
        .eq("causa_id", dados.causa_id)
        .execute()
    )
    if not resultado_causa.data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A causa informada não pertence à não conformidade.",
        )

    ocorrencia_atual = resultado_causa.data[0].get("ocorrencia_numero")
    if ocorrencia_atual is not None and ocorrencia_atual != dados.ocorrencia_gatilho:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A ocorrência informada não corresponde à ocorrência registrada para esta causa.",
        )
    if dados.ocorrencia_gatilho < 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não é possível registrar medida disciplinar antes da quarta ocorrência.",
        )

    if dados.tipo == "suspensao":
        if dados.dias_suspensao is None or not 1 <= dados.dias_suspensao <= 30:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Informe entre 1 e 30 dias para a suspensão.",
            )
    elif dados.dias_suspensao is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A quantidade de dias só deve ser informada para suspensão.",
        )

    existente = (
        servico.table("medidas_disciplinares")
        .select("id")
        .eq("nc_id", dados.nc_id)
        .eq("causa_id", dados.causa_id)
        .eq("ocorrencia_gatilho", dados.ocorrencia_gatilho)
        .execute()
    )
    if existente.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Já existe uma medida registrada para esta NC, causa e ocorrência.",
        )

    resultado = (
        servico.table("medidas_disciplinares")
        .insert(
            {
                "colaborador_id": nc["colaborador_id"],
                "causa_id": dados.causa_id,
                "nc_id": dados.nc_id,
                "ocorrencia_gatilho": dados.ocorrencia_gatilho,
                "tipo": dados.tipo,
                "status": "aplicada",
                "dias_suspensao": dados.dias_suspensao,
                "aplicada_por": usuario.id,
                "observacao": dados.observacao.strip() if dados.observacao else None,
            }
        )
        .execute()
    )
    if not resultado.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Não foi possível registrar a medida disciplinar.",
        )
    return resultado.data[0]
