"""Estatísticas individuais com autorização explícita e ocorrência PR02."""
from datetime import date

from fastapi import HTTPException, status

from app.auth import UsuarioLogado
from app.supabase_client import cliente_servico
from app.recurrence_v2 import (
    STATUS_QUE_CONTAM_REINCIDENCIA,
    inicio_janela_12_meses,
)
from app import nc_service as legacy


def obter_estatisticas_colaborador(
    usuario_logado: UsuarioLogado,
    colaborador_id: str,
) -> dict:
    servico = cliente_servico()
    resultado_usuario = (
        servico.table("usuarios")
        .select("id, nome, setor, papel, supervisor_id")
        .eq("id", colaborador_id)
        .execute()
    )
    if not resultado_usuario.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não encontrado.",
        )

    colaborador = resultado_usuario.data[0]
    eh_proprio = usuario_logado.id == colaborador_id
    eh_supervisor_direto = (
        usuario_logado.papel == "supervisor"
        and colaborador.get("supervisor_id") == usuario_logado.id
    )
    eh_adm = usuario_logado.papel == "adm"

    if not (eh_proprio or eh_supervisor_direto or eh_adm):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Você não tem permissão para ver essas estatísticas.",
        )

    referencia = date.today()
    data_inicial = inicio_janela_12_meses(referencia)
    resultado_ncs = (
        servico.table("nao_conformidades")
        .select("id, data")
        .eq("colaborador_id", colaborador_id)
        .in_("status", STATUS_QUE_CONTAM_REINCIDENCIA)
        .gte("data", data_inicial.isoformat())
        .lte("data", referencia.isoformat())
        .execute()
    )
    ncs_12m = resultado_ncs.data
    ids_ncs_12m = [nc["id"] for nc in ncs_12m]
    data_por_nc = {
        nc["id"]: legacy._parsear_data(nc.get("data")) or date.min
        for nc in ncs_12m
    }

    linhas_causas = []
    if ids_ncs_12m:
        linhas_causas = (
            servico.table("nc_causas")
            .select("nc_id, causa_id, ocorrencia_numero, causas(descricao)")
            .in_("nc_id", ids_ncs_12m)
            .execute()
            .data
        )

    agrupado: dict[int, dict] = {}
    for linha in linhas_causas:
        causa_id = linha["causa_id"]
        descricao = (linha.get("causas") or {}).get("descricao")
        info = agrupado.setdefault(
            causa_id,
            {
                "causa_id": causa_id,
                "causa": descricao,
                "ocorrencias_12m": 0,
                "ultima_ocorrencia_numero": None,
                "ultima_ocorrencia_nc_id": None,
                "medida_sugerida": None,
                "_ultima_chave": None,
            },
        )
        info["ocorrencias_12m"] += 1

        # O snapshot mais recente é escolhido pela data da NC e pelo id como
        # desempate. Não usamos max(ocorrencia_numero), porque a janela móvel
        # pode fazer a numeração diminuir quando ocorrências antigas expiram.
        chave = (data_por_nc.get(linha["nc_id"], date.min), linha["nc_id"])
        if info["_ultima_chave"] is None or chave > info["_ultima_chave"]:
            info["_ultima_chave"] = chave
            info["ultima_ocorrencia_numero"] = linha.get("ocorrencia_numero")
            info["ultima_ocorrencia_nc_id"] = linha["nc_id"]

    for info in agrupado.values():
        info.pop("_ultima_chave", None)
        if eh_adm and info["ultima_ocorrencia_numero"] is not None:
            info["medida_sugerida"] = legacy.decidir_medida_disciplina(
                info["ultima_ocorrencia_numero"]
            )

    medidas = (
        servico.table("medidas_disciplinares")
        .select(
            "id, causa_id, nc_id, ocorrencia_gatilho, tipo, "
            "status, dias_suspensao, data_aplicacao, observacao"
        )
        .eq("colaborador_id", colaborador_id)
        .order("data_aplicacao", desc=True)
        .execute()
        .data
    )
    medidas_por_causa: dict[int, list[dict]] = {}
    for medida in medidas:
        medidas_por_causa.setdefault(medida["causa_id"], []).append(medida)

    causas_saida = []
    for causa_id, info in agrupado.items():
        info["medidas"] = medidas_por_causa.get(causa_id, [])
        causas_saida.append(info)
    causas_saida.sort(key=lambda c: c["ocorrencias_12m"], reverse=True)

    return {
        "usuario_id": colaborador["id"],
        "nome": colaborador["nome"],
        "setor": colaborador.get("setor"),
        "total_nc_12m": len(ids_ncs_12m),
        "causas": causas_saida,
    }
