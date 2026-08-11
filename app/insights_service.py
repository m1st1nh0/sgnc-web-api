"""
Pipeline de indicadores consolidados da página de Insights.

Diferente das rotas normais (que usam o cliente autenticado do usuário e
respeitam o RLS), este endpoint agrega dados de toda a operação. Por isso
usa o cliente de serviço (service_role), que ignora RLS — válido apenas
porque o acesso é restrito a ADM e supervisores (exigir_gestao no router).
"""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from fastapi import HTTPException, status

from app.auth import UsuarioLogado
from app.supabase_client import cliente_servico
from app.nc_service import STATUS_QUE_CONTAM_REINCIDENCIA

PAPEIS_GESTAO = {"adm", "supervisor"}
STATUS_PENDENTES = {"validada", "aguardando_analise", "aguardando_aceite"}
ORDEM_STATUS = [
    "aberta",
    "validada",
    "aguardando_analise",
    "aguardando_aceite",
    "concluida",
    "invalidada",
]
TIPO_MEDIDA_POR_NOME = {
    "advertencias": "advertencia",
    "suspensoes": "suspensao",
    "avaliacoes_justa_causa": "avaliar_justa_causa",
}
MAX_ITENS_POR_LOTE = 900


# =====================================================
# Helpers de data
# =====================================================


def _parsear_data(valor) -> date | None:
    """Converte uma data ISO (date ou datetime serializado) em date."""
    if valor is None:
        return None
    if isinstance(valor, date) and not isinstance(valor, datetime):
        return valor
    texto = str(valor)
    if "T" in texto or len(texto) > 10:
        try:
            return datetime.fromisoformat(texto.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


def _meses_no_intervalo(inicio: date, fim: date) -> list[str]:
    """Lista de chaves 'YYYY-MM' dentro do intervalo, incluindo meses vazios."""
    meses = []
    ano, mes = inicio.year, inicio.month
    while (ano, mes) <= (fim.year, fim.month):
        meses.append(f"{ano:04d}-{mes:02d}")
        mes += 1
        if mes > 12:
            mes = 1
            ano += 1
    return meses


# =====================================================
# Queries auxiliares
# =====================================================


def _consulta_em_lotes(servico, tabela: str, seletor: str, ids, selecao: str) -> list[dict]:
    """Evita estourar o limite do IN do PostgREST quando a lista é grande."""
    if not ids:
        return []
    linhas = []
    for i in range(0, len(ids), MAX_ITENS_POR_LOTE):
        lote = ids[i : i + MAX_ITENS_POR_LOTE]
        resultado = (
            servico.table(tabela)
            .select(selecao)
            .in_(seletor, lote)
            .execute()
        )
        linhas.extend(resultado.data)
    return linhas


def _causas_por_nc(servico, ids_ncs: list[int]) -> dict[int, list[dict]]:
    """Mapa nc_id -> [{causa_id, descricao}] usando o embed causas(descricao)."""
    linhas = _consulta_em_lotes(
        servico,
        "nc_causas",
        "nc_id",
        ids_ncs,
        "nc_id, causa_id, causas(descricao)",
    )
    mapa: dict[int, list[dict]] = defaultdict(list)
    for linha in linhas:
        causa_info = linha.get("causas") or {}
        mapa[linha["nc_id"]].append(
            {
                "causa_id": linha["causa_id"],
                "descricao": causa_info.get("descricao"),
            }
        )
    return mapa


def _descricao_da_causa(servico, causa_id: int, cache: dict[int, str]) -> str:
    if causa_id not in cache:
        resultado = (
            servico.table("causas")
            .select("descricao")
            .eq("id", causa_id)
            .execute()
        )
        cache[causa_id] = (
            resultado.data[0]["descricao"] if resultado.data else f"Causa {causa_id}"
        )
    return cache[causa_id]

# =====================================================
# Endpoint principal
# =====================================================


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

    # ---------- NCs e causas ----------
    resultado_ncs = (
        servico.table("nao_conformidades")
        .select(
            "id, data, status, colaborador_id, colaborador, setor, "
            "criticidade, chamado, reincidencia, aceito_em, "
            "atualizado_em, criado_em"
        )
        .execute()
    )
    todas_ncs = resultado_ncs.data

    def data_efetiva(nc: dict) -> date | None:
        # NCs legadas podem não ter "data"; usamos a data de criação.
        return _parsear_data(nc.get("data")) or _parsear_data(nc.get("criado_em"))

    def data_conclusao(nc: dict) -> date | None:
        return (
            _parsear_data(nc.get("aceito_em"))
            or _parsear_data(nc.get("atualizado_em"))
            or _parsear_data(nc.get("data"))
        )

    ncs_periodo = [
        nc
        for nc in todas_ncs
        if (d := data_efetiva(nc)) is not None and inicio <= d <= fim
    ]
    ncs_concluidas = [nc for nc in todas_ncs if nc.get("status") == "concluida"]

    causas_por_nc = _causas_por_nc(servico, [nc["id"] for nc in todas_ncs])
    cache_descricao: dict[int, str] = {}
    for nc_id, causas in causas_por_nc.items():
        for causa in causas:
            if causa["descricao"]:
                cache_descricao.setdefault(causa["causa_id"], causa["descricao"])

    # ---------- KPIs ----------
    contagem_status = Counter(nc.get("status") for nc in ncs_periodo)
    total = len(ncs_periodo)
    ncs_invalidadas = contagem_status.get("invalidada", 0)

    kpis = {
        "total_ncs": total,
        "ncs_abertas": contagem_status.get("aberta", 0),
        "ncs_pendentes": sum(contagem_status.get(s, 0) for s in STATUS_PENDENTES),
        "ncs_concluidas": contagem_status.get("concluida", 0),
        "ncs_invalidadas": ncs_invalidadas,
        "taxa_invalidacao": round(ncs_invalidadas / total, 4) if total else None,
        "ncs_sem_chamado": sum(
            1 for nc in ncs_periodo if not (nc.get("chamado") or "").strip()
        ),
    }

    # ---------- Por mês (meses vazios entram com zero) ----------
    por_mes = {
        chave: {"mes": chave, "total": 0, "concluidas": 0, "invalidadas": 0}
        for chave in _meses_no_intervalo(inicio, fim)
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

    # ---------- Por status ----------
    presentes = [s for s in ORDEM_STATUS if s in contagem_status]
    extras = [
        s
        for s in contagem_status
        if s not in ORDEM_STATUS and s is not None
    ]
    ncs_por_status = [
        {"status": s, "quantidade": contagem_status[s]}
        for s in presentes + extras
    ]

    # ---------- Por colaborador ----------
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
        agrupados_colaborador.values(),
        key=lambda c: c["total"],
        reverse=True,
    )

    # ---------- Por setor ----------
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
        agrupados_setor.values(),
        key=lambda s: s["total"],
        reverse=True,
    )

    # ---------- Por criticidade ----------
    contagem_criticidade = Counter(
        nc.get("criticidade") or "Não informada" for nc in ncs_periodo
    )
    ncs_por_criticidade = [
        {"criticidade": criticidade, "total": quantidade}
        for criticidade, quantidade in contagem_criticidade.most_common()
    ]

    # ---------- Por causa ----------
    agrupados_causa: dict[int, dict] = {}
    for nc in ncs_periodo:
        for causa in causas_por_nc.get(nc["id"], []):
            causa_id = causa["causa_id"]
            if causa_id not in agrupados_causa:
                agrupados_causa[causa_id] = {
                    "causa_id": causa_id,
                    "causa": _descricao_da_causa(servico, causa_id, cache_descricao),
                    "total": 0,
                    "total_reincidentes": 0,
                }
            agrupados_causa[causa_id]["total"] += 1
            if nc.get("reincidencia") == "Sim":
                agrupados_causa[causa_id]["total_reincidentes"] += 1

    ncs_por_causa = sorted(
        agrupados_causa.values(),
        key=lambda c: c["total"],
        reverse=True,
    )

    # ---------- Medidas disciplinares por causa ----------
    resultado_medidas = (
        servico.table("medidas_disciplinares")
        .select("causa_id, tipo, data_aplicacao")
        .execute()
    )
    agrupados_medidas: dict[int, dict] = {}
    for medida in resultado_medidas.data:
        data_aplicacao = _parsear_data(medida.get("data_aplicacao"))
        if data_aplicacao is not None and not (inicio <= data_aplicacao <= fim):
            continue  # medida fora do período; sem data (legado) entra

        causa_id = medida.get("causa_id")
        if causa_id not in agrupados_medidas:
            agrupados_medidas[causa_id] = {
                "causa_id": causa_id,
                "causa": _descricao_da_causa(servico, causa_id, cache_descricao),
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
        agrupados_medidas.values(),
        key=lambda m: m["total"],
        reverse=True,
    )

    # ---------- Reincidência por causa ----------
    # Conclusões por (causa, colaborador): identifica NCs que voltaram a
    # acontecer DEPOIS de uma NC anterior da mesma causa ser concluída
    # (sinal de que a ação corretiva não resolveu).
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
            if any(d_fim < d_nc for d_fim in conclusoes_por_chave.get(chave, [])):
                reincidiu_por_causa[causa_id] += 1

    reincidencia_por_causa = sorted(
        [
            {
                "causa_id": causa_id,
                "causa": _descricao_da_causa(servico, causa_id, cache_descricao),
                "ocorrencias": ocorrencias_por_causa[causa_id],
                "reincidiu_apos_conclusao": reincidiu_por_causa[causa_id],
            }
            for causa_id in ocorrencias_por_causa
        ],
        key=lambda r: r["ocorrencias"],
        reverse=True,
    )

    # ---------- Resposta ----------
    return {
        "periodo": {
            "inicio": inicio.isoformat(),
            "fim": fim.isoformat(),
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