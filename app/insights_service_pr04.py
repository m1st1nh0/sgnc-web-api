"""PR04: contrato gerencial estável para Insights V2.

Princípios:
- ADM agrega toda a operação;
- supervisor agrega apenas subordinados diretos;
- backlog é fotografia atual do escopo, independente da data de abertura;
- séries de volume usam a data efetiva de abertura no período solicitado;
- tempos usam o timestamp da transição ocorrida no período solicitado;
- reincidência usa exclusivamente nc_causas.ocorrencia_numero > 1.

As chaves legadas principais são preservadas para o frontend atual enquanto
as novas chaves explícitas ficam disponíveis para o redesign do PR06.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from statistics import mean, median

from fastapi import HTTPException, status

from app.auth import UsuarioLogado
from app.supabase_client import cliente_servico
from app import insights_service as legacy
from app import insights_service_v2 as pr01
from app import insights_service_pr02 as pr02
from app.nc_service import decidir_medida_disciplina
from app.recurrence_v2 import STATUS_QUE_CONTAM_REINCIDENCIA, eh_reincidencia
from app.timeline_service import _parse_datetime, calcular_duracoes


PAPEIS_GESTAO = {"adm", "supervisor"}
STATUS_ATIVOS_CANONICOS = {
    "aberta",
    "aguardando_feedback",
    "aguardando_aceite",
    "validada",  # legado -> aguardando_feedback
    "aguardando_analise",  # legado -> aguardando_feedback
}
STATUS_PROCEDENTES = set(STATUS_QUE_CONTAM_REINCIDENCIA)
ORDEM_STATUS_CANONICA = [
    "aberta",
    "aguardando_feedback",
    "aguardando_aceite",
    "concluida",
    "invalidada",
]
FAIXAS_AGING = (
    ("0-1d", 0, 2),
    ("2-3d", 2, 4),
    ("4-7d", 4, 8),
    ("8+d", 8, None),
)


def _normalizar_status(status_nc: str | None) -> str | None:
    if status_nc in {"validada", "aguardando_analise"}:
        return "aguardando_feedback"
    return status_nc


def _subtrair_um_ano(valor: date) -> date:
    try:
        return valor.replace(year=valor.year - 1)
    except ValueError:  # 29/02
        return valor.replace(year=valor.year - 1, day=28)


def _data_efetiva(nc: dict) -> date | None:
    return legacy._parsear_data(nc.get("data")) or legacy._parsear_data(
        nc.get("criado_em")
    )


def _datetime_no_periodo(valor, inicio: date, fim: date) -> bool:
    dt = _parse_datetime(valor)
    return dt is not None and inicio <= dt.date() <= fim


def _consulta_ncs_escopo(servico, equipe_ids: list[str] | None) -> list[dict]:
    consulta = servico.table("nao_conformidades").select(
        "id, data, status, colaborador_id, colaborador, setor, criticidade, "
        "chamado, reincidencia, criado_em, atualizado_em, validado_em, "
        "feedback_aplicado_em, aceito_em, decidido_em, enviado_em"
    )
    if equipe_ids is not None:
        if not equipe_ids:
            return []
        consulta = consulta.in_("colaborador_id", equipe_ids)
    return consulta.execute().data


def _consulta_medidas_escopo(servico, equipe_ids: list[str] | None) -> list[dict]:
    consulta = servico.table("medidas_disciplinares").select(
        "causa_id, colaborador_id, nc_id, ocorrencia_gatilho, tipo, status, "
        "data_aplicacao, criado_em"
    )
    if equipe_ids is not None:
        if not equipe_ids:
            return []
        consulta = consulta.in_("colaborador_id", equipe_ids)
    return consulta.execute().data


def _resumo_tempos(segundos: list[int]) -> dict:
    valores = [int(v) for v in segundos if v is not None and v >= 0]
    if not valores:
        return {
            "amostras": 0,
            "media_segundos": None,
            "mediana_segundos": None,
            "media_horas": None,
            "mediana_horas": None,
        }

    media = round(mean(valores))
    mediana_valor = round(median(valores))
    return {
        "amostras": len(valores),
        "media_segundos": media,
        "mediana_segundos": mediana_valor,
        "media_horas": round(media / 3600, 2),
        "mediana_horas": round(mediana_valor / 3600, 2),
    }


def _inicio_etapa_atual(nc: dict) -> datetime | None:
    status_nc = nc.get("status")
    if status_nc == "aberta":
        return _parse_datetime(nc.get("criado_em"))
    if status_nc in {"validada", "aguardando_analise", "aguardando_feedback"}:
        return (
            _parse_datetime(nc.get("validado_em"))
            or _parse_datetime(nc.get("enviado_em"))
            or _parse_datetime(nc.get("criado_em"))
        )
    if status_nc == "aguardando_aceite":
        return (
            _parse_datetime(nc.get("feedback_aplicado_em"))
            or _parse_datetime(nc.get("validado_em"))
            or _parse_datetime(nc.get("criado_em"))
        )
    return None


def _aged_backlog(ncs_ativas: list[dict], agora: datetime) -> dict:
    contagem_faixa = Counter()
    contagem_status = Counter()
    mais_antiga = None

    for nc in ncs_ativas:
        inicio = _inicio_etapa_atual(nc)
        if inicio is None or inicio > agora:
            continue
        dias = int((agora - inicio).total_seconds() // 86400)
        status_canonico = _normalizar_status(nc.get("status")) or "desconhecido"
        contagem_status[status_canonico] += 1

        for nome, minimo, maximo in FAIXAS_AGING:
            if dias >= minimo and (maximo is None or dias < maximo):
                contagem_faixa[nome] += 1
                break

        if mais_antiga is None or dias > mais_antiga["dias_na_etapa"]:
            mais_antiga = {
                "nc_id": nc["id"],
                "status": status_canonico,
                "dias_na_etapa": dias,
                "desde": inicio.isoformat(),
            }

    return {
        "total": len(ncs_ativas),
        "faixas": [
            {"faixa": nome, "quantidade": contagem_faixa[nome]}
            for nome, _, _ in FAIXAS_AGING
        ],
        "por_status": [
            {"status": status_nc, "quantidade": contagem_status[status_nc]}
            for status_nc in ORDEM_STATUS_CANONICA[:3]
            if contagem_status[status_nc]
        ],
        "mais_antiga": mais_antiga,
    }


def _medidas_e_sugestoes(
    medidas: list[dict],
    causas_por_nc: dict[int, list[dict]],
    ncs_periodo: list[dict],
    inicio: date,
    fim: date,
) -> tuple[dict, list[dict]]:
    aplicadas = Counter()
    for medida in medidas:
        data_medida = legacy._parsear_data(medida.get("data_aplicacao")) or legacy._parsear_data(
            medida.get("criado_em")
        )
        if data_medida is None or not (inicio <= data_medida <= fim):
            continue
        aplicadas[medida.get("tipo") or "nao_informada"] += 1

    sugestoes = Counter()
    por_causa: dict[int, dict] = {}
    for nc in ncs_periodo:
        if nc.get("status") not in STATUS_PROCEDENTES:
            continue
        for causa in causas_por_nc.get(nc["id"], []):
            numero = causa.get("ocorrencia_numero")
            if numero is None:
                continue
            sugestao = decidir_medida_disciplina(numero)
            if sugestao:
                sugestoes[sugestao] += 1
                item = por_causa.setdefault(
                    causa["causa_id"],
                    {
                        "causa_id": causa["causa_id"],
                        "causa": causa.get("descricao") or f"Causa {causa['causa_id']}",
                        "advertencias_sugeridas": 0,
                        "suspensoes_sugeridas": 0,
                        "avaliacoes_justa_causa_sugeridas": 0,
                        "total_sugestoes": 0,
                    },
                )
                item["total_sugestoes"] += 1
                if sugestao == "advertencia":
                    item["advertencias_sugeridas"] += 1
                elif sugestao == "suspensao":
                    item["suspensoes_sugeridas"] += 1
                else:
                    item["avaliacoes_justa_causa_sugeridas"] += 1

    return (
        {
            "aplicadas": {
                "advertencias": aplicadas["advertencia"],
                "suspensoes": aplicadas["suspensao"],
                "avaliacoes_justa_causa": aplicadas["avaliar_justa_causa"],
                "total": sum(aplicadas.values()),
            },
            "sugeridas": {
                "advertencias": sugestoes["advertencia"],
                "suspensoes": sugestoes["suspensao"],
                "avaliacoes_justa_causa": sugestoes["avaliar_justa_causa"],
                "total": sum(sugestoes.values()),
            },
        },
        sorted(por_causa.values(), key=lambda item: item["total_sugestoes"], reverse=True),
    )


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
    inicio = inicio or _subtrair_um_ano(fim)
    if inicio > fim:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A data de início não pode ser posterior à data de fim.",
        )

    servico = cliente_servico()
    equipe_ids = pr01._ids_equipe_direta(servico, usuario)
    todas_ncs = _consulta_ncs_escopo(servico, equipe_ids)

    ncs_periodo = [
        nc
        for nc in todas_ncs
        if (data_nc := _data_efetiva(nc)) is not None and inicio <= data_nc <= fim
    ]
    ncs_ativas = [nc for nc in todas_ncs if nc.get("status") in STATUS_ATIVOS_CANONICOS]

    causas_por_nc = pr02._causas_por_nc_com_ocorrencia(
        servico, [nc["id"] for nc in todas_ncs]
    )
    medidas = _consulta_medidas_escopo(servico, equipe_ids)

    # ---------------- volume e status (compatibilidade + semântica explícita)
    contagem_periodo = Counter(_normalizar_status(nc.get("status")) for nc in ncs_periodo)
    contagem_backlog = Counter(_normalizar_status(nc.get("status")) for nc in ncs_ativas)
    total_periodo = len(ncs_periodo)
    invalidadas_periodo = contagem_periodo["invalidada"]

    concluidas_no_periodo = sum(
        1 for nc in todas_ncs if _datetime_no_periodo(nc.get("aceito_em"), inicio, fim)
    )
    invalidadas_no_periodo = sum(
        1
        for nc in todas_ncs
        if nc.get("status") == "invalidada"
        and _datetime_no_periodo(nc.get("decidido_em"), inicio, fim)
    )

    kpis = {
        # Contrato legado, mantido para o frontend atual.
        "total_ncs": total_periodo,
        "ncs_abertas": contagem_periodo["aberta"],
        "ncs_pendentes": (
            contagem_periodo["aguardando_feedback"]
            + contagem_periodo["aguardando_aceite"]
        ),
        "ncs_concluidas": contagem_periodo["concluida"],
        "ncs_invalidadas": invalidadas_periodo,
        "taxa_invalidacao": round(invalidadas_periodo / total_periodo, 4)
        if total_periodo
        else None,
        "ncs_sem_chamado": sum(
            1 for nc in ncs_periodo if not (nc.get("chamado") or "").strip()
        ),
        # Contrato PR04 explícito.
        "backlog_ativo_atual": len(ncs_ativas),
        "abertas_atuais": contagem_backlog["aberta"],
        "aguardando_feedback_atual": contagem_backlog["aguardando_feedback"],
        "aguardando_aceite_atual": contagem_backlog["aguardando_aceite"],
        "concluidas_no_periodo": concluidas_no_periodo,
        "invalidadas_no_periodo": invalidadas_no_periodo,
    }

    # ---------------- tempos: a amostra pertence ao período pela transição final
    validacao = []
    feedback = []
    aceite = []
    ciclo = []
    decisao = []
    for nc in todas_ncs:
        duracoes = calcular_duracoes(nc)
        if _datetime_no_periodo(nc.get("validado_em"), inicio, fim):
            valor = duracoes.get("criacao_ate_validacao_segundos")
            if valor is not None:
                validacao.append(valor)
        if _datetime_no_periodo(nc.get("feedback_aplicado_em"), inicio, fim):
            valor = duracoes.get("validacao_ate_feedback_segundos")
            if valor is not None:
                feedback.append(valor)
        if _datetime_no_periodo(nc.get("aceito_em"), inicio, fim):
            valor = duracoes.get("feedback_ate_aceite_segundos")
            if valor is not None:
                aceite.append(valor)
            valor = duracoes.get("ciclo_total_segundos")
            if valor is not None:
                ciclo.append(valor)
        if _datetime_no_periodo(nc.get("decidido_em"), inicio, fim):
            valor = duracoes.get("criacao_ate_decisao_segundos")
            if valor is not None:
                decisao.append(valor)

    tempos = {
        "criacao_ate_validacao": _resumo_tempos(validacao),
        "validacao_ate_feedback": _resumo_tempos(feedback),
        "feedback_ate_aceite": _resumo_tempos(aceite),
        "ciclo_total": _resumo_tempos(ciclo),
        "criacao_ate_decisao": _resumo_tempos(decisao),
    }

    agora = datetime.now(timezone.utc)
    aged_backlog = _aged_backlog(ncs_ativas, agora)

    # ---------------- séries temporais
    por_mes = {
        chave: {
            "mes": chave,
            "total": 0,
            "concluidas": 0,
            "invalidadas": 0,
            "reincidentes": 0,
        }
        for chave in legacy._meses_no_intervalo(inicio, fim)
    }
    for nc in ncs_periodo:
        data_nc = _data_efetiva(nc)
        chave = data_nc.strftime("%Y-%m")
        if chave not in por_mes:
            continue
        por_mes[chave]["total"] += 1
        status_nc = _normalizar_status(nc.get("status"))
        if status_nc == "concluida":
            por_mes[chave]["concluidas"] += 1
        elif status_nc == "invalidada":
            por_mes[chave]["invalidadas"] += 1
        if any(
            eh_reincidencia(causa.get("ocorrencia_numero"))
            for causa in causas_por_nc.get(nc["id"], [])
        ):
            por_mes[chave]["reincidentes"] += 1
    ncs_por_mes = [por_mes[chave] for chave in sorted(por_mes)]

    ncs_por_status = [
        {"status": status_nc, "quantidade": contagem_periodo[status_nc]}
        for status_nc in ORDEM_STATUS_CANONICA
        if contagem_periodo[status_nc]
    ]

    # ---------------- colaborador e setor
    por_colaborador: dict[str, dict] = {}
    reincidencia_colaborador = Counter()
    for nc in ncs_periodo:
        chave = nc.get("colaborador_id") or nc.get("colaborador") or "Não informado"
        item = por_colaborador.setdefault(
            chave,
            {
                "colaborador_id": nc.get("colaborador_id"),
                "colaborador": nc.get("colaborador") or "Não informado",
                "setor": nc.get("setor"),
                "total": 0,
                "invalidadas": 0,
                "reincidencias": 0,
                "reincidencias_12m": 0,
                "backlog_ativo": 0,
            },
        )
        item["total"] += 1
        if nc.get("status") == "invalidada":
            item["invalidadas"] += 1
        if any(
            eh_reincidencia(causa.get("ocorrencia_numero"))
            for causa in causas_por_nc.get(nc["id"], [])
        ):
            item["reincidencias"] += 1
            item["reincidencias_12m"] += 1
            reincidencia_colaborador[chave] += 1

    for nc in ncs_ativas:
        chave = nc.get("colaborador_id") or nc.get("colaborador") or "Não informado"
        if chave not in por_colaborador:
            por_colaborador[chave] = {
                "colaborador_id": nc.get("colaborador_id"),
                "colaborador": nc.get("colaborador") or "Não informado",
                "setor": nc.get("setor"),
                "total": 0,
                "invalidadas": 0,
                "reincidencias": 0,
                "reincidencias_12m": 0,
                "backlog_ativo": 0,
            }
        por_colaborador[chave]["backlog_ativo"] += 1

    ncs_por_colaborador = sorted(
        por_colaborador.values(), key=lambda item: (item["total"], item["backlog_ativo"]), reverse=True
    )

    por_setor: dict[str, dict] = {}
    for nc in ncs_periodo:
        setor_nc = nc.get("setor") or "Não informado"
        item = por_setor.setdefault(
            setor_nc,
            {"setor": setor_nc, "total": 0, "invalidadas": 0, "backlog_ativo": 0},
        )
        item["total"] += 1
        if nc.get("status") == "invalidada":
            item["invalidadas"] += 1
    for nc in ncs_ativas:
        setor_nc = nc.get("setor") or "Não informado"
        item = por_setor.setdefault(
            setor_nc,
            {"setor": setor_nc, "total": 0, "invalidadas": 0, "backlog_ativo": 0},
        )
        item["backlog_ativo"] += 1
    ncs_por_setor = sorted(
        por_setor.values(), key=lambda item: (item["total"], item["backlog_ativo"]), reverse=True
    )

    # ---------------- criticidade
    criticidades = Counter(nc.get("criticidade") or "Não informada" for nc in ncs_periodo)
    ncs_por_criticidade = [
        {"criticidade": nome, "total": quantidade}
        for nome, quantidade in criticidades.most_common()
    ]

    # ---------------- causas e reincidência canônica
    por_causa: dict[int, dict] = {}
    recorrencias_causa = Counter()
    ocorrencias_causa = Counter()
    for nc in ncs_periodo:
        if nc.get("status") not in STATUS_PROCEDENTES:
            # Causas de aberta/invalidada continuam úteis no volume geral,
            # mas não entram na série canônica de ocorrência/reincidência.
            for causa in causas_por_nc.get(nc["id"], []):
                item = por_causa.setdefault(
                    causa["causa_id"],
                    {
                        "causa_id": causa["causa_id"],
                        "causa": causa.get("descricao") or f"Causa {causa['causa_id']}",
                        "total": 0,
                        "total_reincidentes": 0,
                    },
                )
                item["total"] += 1
            continue

        for causa in causas_por_nc.get(nc["id"], []):
            item = por_causa.setdefault(
                causa["causa_id"],
                {
                    "causa_id": causa["causa_id"],
                    "causa": causa.get("descricao") or f"Causa {causa['causa_id']}",
                    "total": 0,
                    "total_reincidentes": 0,
                },
            )
            item["total"] += 1
            ocorrencias_causa[causa["causa_id"]] += 1
            if eh_reincidencia(causa.get("ocorrencia_numero")):
                item["total_reincidentes"] += 1
                recorrencias_causa[causa["causa_id"]] += 1

    ncs_por_causa = sorted(por_causa.values(), key=lambda item: item["total"], reverse=True)
    reincidencia_por_causa = sorted(
        [
            {
                "causa_id": causa_id,
                "causa": por_causa.get(causa_id, {}).get("causa") or f"Causa {causa_id}",
                "ocorrencias": ocorrencias_causa[causa_id],
                "reincidencias_12m": recorrencias_causa[causa_id],
                # alias legado até o PR06 retirar a nomenclatura antiga
                "reincidiu_apos_conclusao": recorrencias_causa[causa_id],
            }
            for causa_id in ocorrencias_causa
        ],
        key=lambda item: item["ocorrencias"],
        reverse=True,
    )

    reincidencia_por_colaborador = sorted(
        [
            {
                "colaborador_id": item.get("colaborador_id"),
                "colaborador": item.get("colaborador"),
                "setor": item.get("setor"),
                "reincidencias_12m": item.get("reincidencias_12m", 0),
                "total_ncs": item.get("total", 0),
            }
            for item in ncs_por_colaborador
            if item.get("reincidencias_12m", 0) > 0
        ],
        key=lambda item: item["reincidencias_12m"],
        reverse=True,
    )

    # ---------------- medidas disciplinares aplicadas + gatilhos sugeridos
    resumo_disciplina, sugestoes_por_causa = _medidas_e_sugestoes(
        medidas, causas_por_nc, ncs_periodo, inicio, fim
    )

    # Mantém o agrupamento legado de medidas por causa.
    medidas_por_causa: dict[int, dict] = {}
    for medida in medidas:
        data_medida = legacy._parsear_data(medida.get("data_aplicacao")) or legacy._parsear_data(
            medida.get("criado_em")
        )
        if data_medida is None or not (inicio <= data_medida <= fim):
            continue
        causa_id = medida.get("causa_id")
        item = medidas_por_causa.setdefault(
            causa_id,
            {
                "causa_id": causa_id,
                "causa": por_causa.get(causa_id, {}).get("causa") or f"Causa {causa_id}",
                "advertencias": 0,
                "suspensoes": 0,
                "avaliacoes_justa_causa": 0,
                "total": 0,
            },
        )
        item["total"] += 1
        if medida.get("tipo") == "advertencia":
            item["advertencias"] += 1
        elif medida.get("tipo") == "suspensao":
            item["suspensoes"] += 1
        elif medida.get("tipo") == "avaliar_justa_causa":
            item["avaliacoes_justa_causa"] += 1

    return {
        "versao_contrato": "insights-v2",
        "periodo": {"inicio": inicio.isoformat(), "fim": fim.isoformat()},
        "escopo": {
            "tipo": "global" if equipe_ids is None else "equipe_direta",
            "quantidade_colaboradores": None if equipe_ids is None else len(equipe_ids),
        },
        "metodologia": {
            "volume": "NCs cuja data efetiva de abertura está dentro do período.",
            "backlog": "Fotografia atual de todas as NCs ativas do escopo, independentemente da data de abertura.",
            "tempos": "Cada amostra pertence ao período pelo timestamp da transição final medida.",
            "reincidencia": "Mesmo colaborador + mesma causa; snapshot ocorrencia_numero > 1 na janela móvel de 12 meses.",
        },
        "kpis": kpis,
        "tempos": tempos,
        "aged_backlog": aged_backlog,
        "ncs_por_mes": ncs_por_mes,
        "ncs_por_status": ncs_por_status,
        "ncs_por_colaborador": ncs_por_colaborador,
        "ncs_por_setor": ncs_por_setor,
        "ncs_por_criticidade": ncs_por_criticidade,
        "ncs_por_causa": ncs_por_causa,
        "reincidencia_por_causa": reincidencia_por_causa,
        "reincidencia_por_colaborador": reincidencia_por_colaborador,
        "medidas_por_causa": sorted(
            medidas_por_causa.values(), key=lambda item: item["total"], reverse=True
        ),
        "disciplina": resumo_disciplina,
        "sugestoes_disciplinares_por_causa": sugestoes_por_causa,
    }
