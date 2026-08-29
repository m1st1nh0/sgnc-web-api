"""Ajusta apenas as métricas de reincidência dos Insights para a regra PR02.

O contrato geral de Insights continua sendo o da PR01 e será redesenhado no
PR04. Aqui removemos a dependência do booleano legado da NC e usamos o
snapshot por causa em ``nc_causas.ocorrencia_numero``.
"""
from collections import Counter, defaultdict
from datetime import date

from app.auth import UsuarioLogado
from app import insights_service as legacy
from app import insights_service_v2 as base
from app.recurrence_v2 import STATUS_QUE_CONTAM_REINCIDENCIA, eh_reincidencia


def _causas_por_nc_com_ocorrencia(servico, ids_ncs: list[int]) -> dict[int, list[dict]]:
    linhas = legacy._consulta_em_lotes(
        servico,
        "nc_causas",
        "nc_id",
        ids_ncs,
        "nc_id, causa_id, ocorrencia_numero, causas(descricao)",
    )
    mapa: dict[int, list[dict]] = defaultdict(list)
    for linha in linhas:
        causa_info = linha.get("causas") or {}
        mapa[linha["nc_id"]].append(
            {
                "causa_id": linha["causa_id"],
                "descricao": causa_info.get("descricao"),
                "ocorrencia_numero": linha.get("ocorrencia_numero"),
            }
        )
    return mapa


def obter_insights(
    usuario: UsuarioLogado,
    inicio: date | None,
    fim: date | None,
) -> dict:
    resposta = base.obter_insights(usuario, inicio, fim)

    inicio_efetivo = date.fromisoformat(resposta["periodo"]["inicio"])
    fim_efetivo = date.fromisoformat(resposta["periodo"]["fim"])
    # Reutiliza o mesmo ponto de injeção do serviço PR01. Isso mantém testes
    # offline determinísticos e evita abrir clientes diferentes na mesma leitura.
    servico = base.cliente_servico()
    equipe_ids = base._ids_equipe_direta(servico, usuario)
    todas_ncs = base._consulta_ncs_escopo(servico, equipe_ids)

    def data_efetiva(nc: dict) -> date | None:
        return legacy._parsear_data(nc.get("data")) or legacy._parsear_data(
            nc.get("criado_em")
        )

    ncs_periodo = [
        nc
        for nc in todas_ncs
        if (d := data_efetiva(nc)) is not None
        and inicio_efetivo <= d <= fim_efetivo
    ]
    ncs_por_id = {nc["id"]: nc for nc in ncs_periodo}
    causas_por_nc = _causas_por_nc_com_ocorrencia(
        servico, list(ncs_por_id)
    )

    # Colaborador: uma NC conta como reincidente quando ao menos uma das
    # causas já está na segunda ocorrência ou acima.
    reincidencias_por_colaborador: Counter = Counter()
    for nc in ncs_periodo:
        if any(
            eh_reincidencia(causa.get("ocorrencia_numero"))
            for causa in causas_por_nc.get(nc["id"], [])
        ):
            chave = nc.get("colaborador_id") or nc.get("colaborador") or "Não informado"
            reincidencias_por_colaborador[chave] += 1

    for item in resposta.get("ncs_por_colaborador", []):
        chave = item.get("colaborador_id") or item.get("colaborador") or "Não informado"
        item["reincidencias"] = reincidencias_por_colaborador[chave]

    # Causa: reincidência é individual por relação NC-causa. Uma segunda
    # causa inédita na mesma NC não herda a reincidência da primeira.
    reincidentes_por_causa: Counter = Counter()
    for nc in ncs_periodo:
        for causa in causas_por_nc.get(nc["id"], []):
            if eh_reincidencia(causa.get("ocorrencia_numero")):
                reincidentes_por_causa[causa["causa_id"]] += 1

    for item in resposta.get("ncs_por_causa", []):
        item["total_reincidentes"] = reincidentes_por_causa[item["causa_id"]]

    # Série específica de reincidência: mantém a chave legada
    # ``reincidiu_apos_conclusao`` para não quebrar o frontend do PR01, mas
    # seu valor agora segue ocorrência > 1. O alias novo explicita a semântica.
    ocorrencias_por_causa: Counter = Counter()
    recorrencias_por_causa: Counter = Counter()
    descricao_por_causa: dict[int, str] = {}

    for nc in ncs_periodo:
        if nc.get("status") not in STATUS_QUE_CONTAM_REINCIDENCIA:
            continue
        for causa in causas_por_nc.get(nc["id"], []):
            causa_id = causa["causa_id"]
            descricao_por_causa.setdefault(
                causa_id, causa.get("descricao") or f"Causa {causa_id}"
            )
            ocorrencias_por_causa[causa_id] += 1
            if eh_reincidencia(causa.get("ocorrencia_numero")):
                recorrencias_por_causa[causa_id] += 1

    resposta["reincidencia_por_causa"] = sorted(
        [
            {
                "causa_id": causa_id,
                "causa": descricao_por_causa[causa_id],
                "ocorrencias": ocorrencias_por_causa[causa_id],
                "reincidiu_apos_conclusao": recorrencias_por_causa[causa_id],
                "reincidencias_12m": recorrencias_por_causa[causa_id],
            }
            for causa_id in ocorrencias_por_causa
        ],
        key=lambda item: item["ocorrencias"],
        reverse=True,
    )

    return resposta
