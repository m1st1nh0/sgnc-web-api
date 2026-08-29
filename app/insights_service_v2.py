"""Insights V2 com escopo explícito por papel.

ADM agrega toda a operação. Supervisor agrega exclusivamente os seus
subordinados diretos (usuarios.supervisor_id = supervisor logado).
"""
from collections import Counter, defaultdict
from datetime import date, timedelta

from fastapi import HTTPException, status

from app.auth import UsuarioLogado
from app.supabase_client import cliente_servico
from app import insights_service as legacy
from app.nc_service_v2 import STATUS_QUE_CONTAM_REINCIDENCIA

PAPEIS_GESTAO = {"adm", "supervisor"}
STATUS_PENDENTES = {
    "aguardando_feedback",
    "aguardando_aceite",
    "validada",  # legado
    "aguardando_analise",  # legado
}
ORDEM_STATUS = [
    "aberta",
    "aguardando_feedback",
    "aguardando_aceite",
    "concluida",
    "invalidada",
    "validada",  # legado
    "aguardando_analise",  # legado
]
TIPO_MEDIDA_POR_NOME = legacy.TIPO_MEDIDA_POR_NOME


def _ids_equipe_direta(servico, usuario: UsuarioLogado) -> list[str] | None:
    """None significa escopo global (ADM); lista significa equipe do supervisor."""
    if usuario.papel == "adm":
        return None
    resultado = (
        servico.table("usuarios")
        .select("id")
        .eq("supervisor_id", usuario.id)
        .execute()
    )
    return [linha["id"] for linha in resultado.data]


def _consulta_ncs_escopo(servico, equipe_ids: list[str] | None) -> list[dict]:
    consulta = servico.table("nao_conformidades").select(
        "id, data, status, colaborador_id, colaborador, setor, "
        "criticidade, chamado, reincidencia, aceito_em, "
        "atualizado_em, criado_em"
    )
    if equipe_ids is not None:
        if not equipe_ids:
            return []
        consulta = consulta.in_("colaborador_id", equipe_ids)
    return consulta.execute().data


def _consulta_medidas_escopo(servico, equipe_ids: list[str] | None) -> list[dict]:
    consulta = servico.table("medidas_disciplinares").select(
        "causa_id, colaborador_id, tipo, data_aplicacao"
    )
    if equipe_ids is not None:
        if not equipe_ids:
            return []
        consulta = consulta.in_("colaborador_id", equipe_ids)
    return consulta.execute().data


def obter_insights(
    usuario: UsuarioLogado,
    inicio: date | None,
    fim: date | None,
) -> dict:
    if usuario.papel not in PAPEIS_GESTAO:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito a administradores e supervisores.",
        )

    fim = fim or date.today()
    inicio = inicio or (fim - timedelta(days=365))
    if inicio > fim:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A data de início não pode ser posterior à data de fim.",
        )

    servico = cliente_servico()
    equipe_ids = _ids_equipe_direta(servico, usuario)
    todas_ncs = _consulta_ncs_escopo(servico, equipe_ids)

    def data_efetiva(nc: dict) -> date | None:
        return legacy._parsear_data(nc.get("data")) or legacy._parsear_data(
            nc.get("criado_em")
        )

    def data_conclusao(nc: dict) -> date | None:
        return (
            legacy._parsear_data(nc.get("aceito_em"))
            or legacy._parsear_data(nc.get("atualizado_em"))
            or legacy._parsear_data(nc.get("data"))
        )

    ncs_periodo = [
        nc
        for nc in todas_ncs
        if (d := data_efetiva(nc)) is not None and inicio <= d <= fim
    ]
    ncs_concluidas = [
        nc for nc in todas_ncs if nc.get("status") == "concluida"
    ]

    causas_por_nc = legacy._causas_por_nc(
        servico, [nc["id"] for nc in todas_ncs]
    )
    cache_descricao: dict[int, str] = {}
    for causas in causas_por_nc.values():
        for causa in causas:
            if causa["descricao"]:
                cache_descricao.setdefault(
                    causa["causa_id"], causa["descricao"]
                )

    contagem_status = Counter(nc.get("status") for nc in ncs_periodo)
    total = len(ncs_periodo)
    ncs_invalidadas = contagem_status.get("invalidada", 0)
    kpis = {
        "total_ncs": total,
        "ncs_abertas": contagem_status.get("aberta", 0),
        "ncs_pendentes": sum(
            contagem_status.get(s, 0) for s in STATUS_PENDENTES
        ),
        "ncs_concluidas": contagem_status.get("concluida", 0),
        "ncs_invalidadas": ncs_invalidadas,
        "taxa_invalidacao": round(ncs_invalidadas / total, 4) if total else None,
        "ncs_sem_chamado": sum(
            1 for nc in ncs_periodo if not (nc.get("chamado") or "").strip()
        ),
    }

    por_mes = {
        chave: {"mes": chave, "total": 0, "concluidas": 0, "invalidadas": 0}
        for chave in legacy._meses_no_intervalo(inicio, fim)
    }
    for nc in ncs_periodo:
        chave_mes = data_efetiva(nc).strftime("%Y-%m")
        if chave_mes not in por_mes:
            continue
        por_mes[chave_mes]["total"] += 1
        if nc.get("status") == "concluida":
            por_mes[chave_mes]["concluidas"] += 1
        elif nc.get("status") == "invalidada":
            por_mes[chave_mes]["invalidadas"] += 1
    ncs_por_mes = [por_mes[chave] for chave in sorted(por_mes)]

    presentes = [s for s in ORDEM_STATUS if s in contagem_status]
    extras = [
        s for s in contagem_status if s not in ORDEM_STATUS and s is not None
    ]
    ncs_por_status = [
        {"status": s, "quantidade": contagem_status[s]}
        for s in presentes + extras
    ]

    agrupados_colaborador: dict[str, dict] = {}
    for nc in ncs_periodo:
        chave = nc.get("colaborador_id") or nc.get("colaborador") or "Não informado"
        dado = agrupados_colaborador.setdefault(
            chave,
            {
                "colaborador_id": nc.get("colaborador_id"),
                "colaborador": nc.get("colaborador") or "Não informado",
                "setor": nc.get("setor"),
                "total": 0,
                "invalidadas": 0,
                "reincidencias": 0,
            },
        )
        dado["total"] += 1
        if nc.get("status") == "invalidada":
            dado["invalidadas"] += 1
        if nc.get("reincidencia") == "Sim":
            dado["reincidencias"] += 1
    ncs_por_colaborador = sorted(
        agrupados_colaborador.values(), key=lambda c: c["total"], reverse=True
    )

    agrupados_setor: dict[str, dict] = {}
    for nc in ncs_periodo:
        chave = nc.get("setor") or "Não informado"
        dado = agrupados_setor.setdefault(
            chave, {"setor": chave, "total": 0, "invalidadas": 0}
        )
        dado["total"] += 1
        if nc.get("status") == "invalidada":
            dado["invalidadas"] += 1
    ncs_por_setor = sorted(
        agrupados_setor.values(), key=lambda s: s["total"], reverse=True
    )

    contagem_criticidade = Counter(
        nc.get("criticidade") or "Não informada" for nc in ncs_periodo
    )
    ncs_por_criticidade = [
        {"criticidade": criticidade, "total": quantidade}
        for criticidade, quantidade in contagem_criticidade.most_common()
    ]

    agrupados_causa: dict[int, dict] = {}
    for nc in ncs_periodo:
        for causa in causas_por_nc.get(nc["id"], []):
            causa_id = causa["causa_id"]
            if causa_id not in agrupados_causa:
                agrupados_causa[causa_id] = {
                    "causa_id": causa_id,
                    "causa": legacy._descricao_da_causa(
                        servico, causa_id, cache_descricao
                    ),
                    "total": 0,
                    "total_reincidentes": 0,
                }
            agrupados_causa[causa_id]["total"] += 1
            if nc.get("reincidencia") == "Sim":
                agrupados_causa[causa_id]["total_reincidentes"] += 1
    ncs_por_causa = sorted(
        agrupados_causa.values(), key=lambda c: c["total"], reverse=True
    )

    agrupados_medidas: dict[int, dict] = {}
    for medida in _consulta_medidas_escopo(servico, equipe_ids):
        data_aplicacao = legacy._parsear_data(medida.get("data_aplicacao"))
        if data_aplicacao is not None and not (inicio <= data_aplicacao <= fim):
            continue
        causa_id = medida.get("causa_id")
        if causa_id not in agrupados_medidas:
            agrupados_medidas[causa_id] = {
                "causa_id": causa_id,
                "causa": legacy._descricao_da_causa(
                    servico, causa_id, cache_descricao
                ),
                "advertencias": 0,
                "suspensoes": 0,
                "avaliacoes_justa_causa": 0,
                "total": 0,
            }
        agrupados_medidas[causa_id]["total"] += 1
        for nome, tipo in TIPO_MEDIDA_POR_NOME.items():
            if medida.get("tipo") == tipo:
                agrupados_medidas[causa_id][nome] += 1
                break
    medidas_por_causa = sorted(
        agrupados_medidas.values(), key=lambda m: m["total"], reverse=True
    )

    conclusoes_por_chave: dict[tuple, list[date]] = defaultdict(list)
    for nc in ncs_concluidas:
        data_fim = data_conclusao(nc)
        if data_fim is None:
            continue
        for causa in causas_por_nc.get(nc["id"], []):
            chave = (causa["causa_id"], nc.get("colaborador_id"))
            conclusoes_por_chave[chave].append(data_fim)
    for lista in conclusoes_por_chave.values():
        lista.sort()

    ocorrencias_por_causa: Counter = Counter()
    reincidiu_por_causa: Counter = Counter()
    for nc in ncs_periodo:
        if nc.get("status") not in STATUS_QUE_CONTAM_REINCIDENCIA:
            continue
        d_nc = data_efetiva(nc)
        for causa in causas_por_nc.get(nc["id"], []):
            causa_id = causa["causa_id"]
            ocorrencias_por_causa[causa_id] += 1
            chave = (causa_id, nc.get("colaborador_id"))
            if any(
                d_fim < d_nc for d_fim in conclusoes_por_chave.get(chave, [])
            ):
                reincidiu_por_causa[causa_id] += 1

    reincidencia_por_causa = sorted(
        [
            {
                "causa_id": causa_id,
                "causa": legacy._descricao_da_causa(
                    servico, causa_id, cache_descricao
                ),
                "ocorrencias": ocorrencias_por_causa[causa_id],
                "reincidiu_apos_conclusao": reincidiu_por_causa[causa_id],
            }
            for causa_id in ocorrencias_por_causa
        ],
        key=lambda r: r["ocorrencias"],
        reverse=True,
    )

    return {
        "periodo": {"inicio": inicio.isoformat(), "fim": fim.isoformat()},
        "escopo": {
            "tipo": "global" if equipe_ids is None else "equipe_direta",
            "quantidade_colaboradores": None if equipe_ids is None else len(equipe_ids),
        },
        "kpis": kpis,
        "ncs_por_mes": ncs_por_mes,
        "ncs_por_status": ncs_por_status,
        "ncs_por_colaborador": ncs_por_colaborador,
        "ncs_por_setor": ncs_por_setor,
        "ncs_por_criticidade": ncs_por_criticidade,
        "ncs_por_causa": ncs_por_causa,
        "medidas_por_causa": medidas_por_causa,
        "reincidencia_por_causa": reincidencia_por_causa,
    }
