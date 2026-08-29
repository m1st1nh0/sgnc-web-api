"""PR07: relatórios gerenciais do SGNC.

O relatório detalhado (CSV) e o resumo gerencial (PDF) seguem o mesmo escopo
já consolidado nos Insights V2:
- ADM: organização inteira;
- supervisor: somente subordinados diretos.

Nenhuma regra de recorrência ou KPI é recalculada de forma paralela ao domínio:
o CSV usa ``nc_causas.ocorrencia_numero`` e o PDF consome o contrato PR04.
"""
from __future__ import annotations

import csv
from datetime import date
from html import escape
from io import BytesIO, StringIO

from fastapi import HTTPException, status
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.auth import UsuarioLogado
from app.supabase_client import cliente_servico
from app import insights_service_pr04 as insights_service
from app import insights_service_v2 as pr01
from app import insights_service_pr02 as pr02
from app.timeline_service import calcular_duracoes


STATUS_CANONICOS = {
    "aberta",
    "aguardando_feedback",
    "aguardando_aceite",
    "concluida",
    "invalidada",
}


def _normalizar_status(valor: str | None) -> str | None:
    if valor in {"validada", "aguardando_analise"}:
        return "aguardando_feedback"
    return valor


def _subtrair_um_ano(valor: date) -> date:
    try:
        return valor.replace(year=valor.year - 1)
    except ValueError:
        return valor.replace(year=valor.year - 1, day=28)


def _resolver_periodo(inicio: date | None, fim: date | None) -> tuple[date, date]:
    fim_final = fim or date.today()
    inicio_final = inicio or _subtrair_um_ano(fim_final)
    if inicio_final > fim_final:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A data de início não pode ser posterior à data de fim.",
        )
    return inicio_final, fim_final


def _validar_status(status_filtro: str | None) -> str | None:
    if not status_filtro:
        return None
    normalizado = _normalizar_status(status_filtro.strip().lower())
    if normalizado not in STATUS_CANONICOS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Status de relatório inválido.",
        )
    return normalizado


def _data_efetiva(nc: dict) -> date | None:
    return insights_service._data_efetiva(nc)


def _consulta_ncs_escopo(servico, equipe_ids: list[str] | None) -> list[dict]:
    consulta = servico.table("nao_conformidades").select(
        "id, data, status, colaborador_id, colaborador, setor, criticidade, "
        "chamado, descricao, criado_em, atualizado_em, validado_em, "
        "feedback_aplicado_em, aceito_em"
    )
    if equipe_ids is not None:
        if not equipe_ids:
            return []
        consulta = consulta.in_("colaborador_id", equipe_ids)
    return consulta.execute().data


def _filtrar_ncs(
    ncs: list[dict],
    inicio: date,
    fim: date,
    status_filtro: str | None = None,
    colaborador_id: str | None = None,
    setor: str | None = None,
) -> list[dict]:
    status_canonico = _validar_status(status_filtro)
    setor_normalizado = setor.strip().casefold() if setor and setor.strip() else None

    filtradas = []
    for nc in ncs:
        data_nc = _data_efetiva(nc)
        if data_nc is None or not (inicio <= data_nc <= fim):
            continue
        if status_canonico and _normalizar_status(nc.get("status")) != status_canonico:
            continue
        if colaborador_id and nc.get("colaborador_id") != colaborador_id:
            continue
        if setor_normalizado and (nc.get("setor") or "").strip().casefold() != setor_normalizado:
            continue
        filtradas.append(nc)

    return sorted(
        filtradas,
        key=lambda nc: (_data_efetiva(nc) or date.min, int(nc.get("id") or 0)),
        reverse=True,
    )


def _causas_texto(causas: list[dict]) -> tuple[str, str]:
    nomes = []
    ocorrencias = []
    for causa in causas:
        descricao = causa.get("descricao") or f"Causa {causa.get('causa_id')}"
        nomes.append(str(descricao))
        numero = causa.get("ocorrencia_numero")
        if numero is not None:
            ocorrencias.append(f"{descricao} (#{numero})")
    return ", ".join(nomes), ", ".join(ocorrencias)


def _horas(segundos) -> str:
    if segundos is None:
        return ""
    return f"{max(0, int(segundos)) / 3600:.2f}".replace(".", ",")


def _montar_csv(ncs: list[dict], causas_por_nc: dict[int, list[dict]]) -> bytes:
    buffer = StringIO(newline="")
    escritor = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    escritor.writerow(
        [
            "NC",
            "Data",
            "Status",
            "Colaborador",
            "Setor",
            "Criticidade",
            "Chamado",
            "Causas",
            "Ocorrências por causa",
            "Reincidente 12m",
            "Descrição",
            "Criado em",
            "Validado em",
            "Feedback aplicado em",
            "Aceito em",
            "Horas até validação",
            "Horas validação-feedback",
            "Horas feedback-aceite",
            "Horas ciclo total",
        ]
    )

    for nc in ncs:
        causas = causas_por_nc.get(nc["id"], [])
        causas_texto, ocorrencias_texto = _causas_texto(causas)
        reincidente = any((c.get("ocorrencia_numero") or 0) > 1 for c in causas)
        duracoes = calcular_duracoes(nc)
        escritor.writerow(
            [
                nc.get("id"),
                (_data_efetiva(nc) or "").isoformat() if _data_efetiva(nc) else "",
                _normalizar_status(nc.get("status")) or "",
                nc.get("colaborador") or "",
                nc.get("setor") or "",
                nc.get("criticidade") or "",
                nc.get("chamado") or "",
                causas_texto,
                ocorrencias_texto,
                "Sim" if reincidente else "Não",
                nc.get("descricao") or "",
                nc.get("criado_em") or "",
                nc.get("validado_em") or "",
                nc.get("feedback_aplicado_em") or "",
                nc.get("aceito_em") or "",
                _horas(duracoes.get("criacao_ate_validacao_segundos")),
                _horas(duracoes.get("validacao_ate_feedback_segundos")),
                _horas(duracoes.get("feedback_ate_aceite_segundos")),
                _horas(duracoes.get("ciclo_total_segundos")),
            ]
        )

    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def gerar_csv_detalhado(
    usuario: UsuarioLogado,
    inicio: date | None,
    fim: date | None,
    status_filtro: str | None = None,
    colaborador_id: str | None = None,
    setor: str | None = None,
) -> tuple[bytes, str]:
    inicio_final, fim_final = _resolver_periodo(inicio, fim)
    servico = cliente_servico()
    equipe_ids = pr01._ids_equipe_direta(servico, usuario)
    ncs_escopo = _consulta_ncs_escopo(servico, equipe_ids)
    ncs = _filtrar_ncs(
        ncs_escopo,
        inicio_final,
        fim_final,
        status_filtro=status_filtro,
        colaborador_id=colaborador_id,
        setor=setor,
    )
    causas_por_nc = pr02._causas_por_nc_com_ocorrencia(
        servico, [nc["id"] for nc in ncs]
    )
    nome = f"sgnc-ncs-{inicio_final.isoformat()}-{fim_final.isoformat()}.csv"
    return _montar_csv(ncs, causas_por_nc), nome


def _fmt_numero(valor) -> str:
    return "—" if valor is None else str(valor)


def _fmt_horas_resumo(resumo: dict | None) -> str:
    if not resumo or resumo.get("mediana_horas") is None:
        return "—"
    return f"{resumo['mediana_horas']:.2f} h".replace(".", ",")


def _tabela(dados, larguras=None):
    tabela = Table(dados, colWidths=larguras, repeatRows=1)
    tabela.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return tabela


def _montar_pdf(dados: dict, usuario: UsuarioLogado) -> bytes:
    buffer = BytesIO()
    documento = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title="SGNC - Relatório gerencial",
        author=usuario.nome,
    )
    estilos = getSampleStyleSheet()
    historia = [
        Paragraph("SGNC — Relatório gerencial", estilos["Title"]),
        Paragraph(
            f"Período: {escape(dados['periodo']['inicio'])} a {escape(dados['periodo']['fim'])}",
            estilos["Normal"],
        ),
        Paragraph(
            "Escopo: organização inteira"
            if dados.get("escopo", {}).get("tipo") == "global"
            else f"Escopo: equipe direta ({dados.get('escopo', {}).get('quantidade_colaboradores', 0)} colaboradores)",
            estilos["Normal"],
        ),
        Spacer(1, 5 * mm),
    ]

    kpis = dados.get("kpis", {})
    historia.append(Paragraph("Operação atual", estilos["Heading2"]))
    historia.append(
        _tabela(
            [
                ["Backlog ativo", "Avaliação", "Feedback", "Aceite", "Mais antiga"],
                [
                    _fmt_numero(kpis.get("backlog_ativo_atual")),
                    _fmt_numero(kpis.get("abertas_atuais")),
                    _fmt_numero(kpis.get("aguardando_feedback_atual")),
                    _fmt_numero(kpis.get("aguardando_aceite_atual")),
                    (
                        f"NC #{dados['aged_backlog']['mais_antiga']['nc_id']} — "
                        f"{dados['aged_backlog']['mais_antiga']['dias_na_etapa']}d"
                        if dados.get("aged_backlog", {}).get("mais_antiga")
                        else "—"
                    ),
                ],
            ],
            [35 * mm, 35 * mm, 35 * mm, 35 * mm, 55 * mm],
        )
    )
    historia.append(Spacer(1, 4 * mm))

    tempos = dados.get("tempos", {})
    historia.append(Paragraph("Medianas de tempo", estilos["Heading2"]))
    historia.append(
        _tabela(
            [
                ["Até validação", "Validação → feedback", "Feedback → aceite", "Ciclo total"],
                [
                    _fmt_horas_resumo(tempos.get("criacao_ate_validacao")),
                    _fmt_horas_resumo(tempos.get("validacao_ate_feedback")),
                    _fmt_horas_resumo(tempos.get("feedback_ate_aceite")),
                    _fmt_horas_resumo(tempos.get("ciclo_total")),
                ],
            ],
            [52 * mm, 52 * mm, 52 * mm, 52 * mm],
        )
    )
    historia.append(Spacer(1, 4 * mm))

    historia.append(Paragraph("Volume no período", estilos["Heading2"]))
    taxa = kpis.get("taxa_invalidacao")
    taxa_texto = "—" if taxa is None else f"{taxa * 100:.1f}%".replace(".", ",")
    historia.append(
        _tabela(
            [
                ["Registradas", "Concluídas", "Invalidadas", "Taxa invalidação"],
                [
                    _fmt_numero(kpis.get("total_ncs")),
                    _fmt_numero(kpis.get("concluidas_no_periodo")),
                    _fmt_numero(kpis.get("invalidadas_no_periodo")),
                    taxa_texto,
                ],
            ],
            [52 * mm, 52 * mm, 52 * mm, 52 * mm],
        )
    )
    historia.append(Spacer(1, 4 * mm))

    causas = (dados.get("ncs_por_causa") or [])[:8]
    historia.append(Paragraph("Principais causas", estilos["Heading2"]))
    if causas:
        linhas = [["Causa", "Ocorrências", "Reincidentes 12m"]]
        for item in causas:
            linhas.append(
                [
                    Paragraph(escape(str(item.get("causa") or "Não informada")), estilos["BodyText"]),
                    _fmt_numero(item.get("total")),
                    _fmt_numero(item.get("total_reincidentes")),
                ]
            )
        historia.append(_tabela(linhas, [120 * mm, 42 * mm, 50 * mm]))
    else:
        historia.append(Paragraph("Sem ocorrências no período.", estilos["Normal"]))

    disciplina = dados.get("disciplina", {})
    aplicadas = disciplina.get("aplicadas", {})
    sugeridas = disciplina.get("sugeridas", {})
    historia.append(Spacer(1, 4 * mm))
    historia.append(Paragraph("Disciplina", estilos["Heading2"]))
    historia.append(
        _tabela(
            [
                ["Medidas aplicadas", "Advertências", "Suspensões", "Gatilhos sugeridos"],
                [
                    _fmt_numero(aplicadas.get("total")),
                    _fmt_numero(aplicadas.get("advertencias")),
                    _fmt_numero(aplicadas.get("suspensoes")),
                    _fmt_numero(sugeridas.get("total")),
                ],
            ],
            [52 * mm, 52 * mm, 52 * mm, 52 * mm],
        )
    )

    historia.append(Spacer(1, 5 * mm))
    historia.append(
        Paragraph(
            "Metodologia: volume por data de abertura; backlog como fotografia atual; "
            "tempos associados ao período pela transição final; reincidência pelo snapshot "
            "canônico de 12 meses.",
            estilos["Italic"],
        )
    )
    documento.build(historia)
    return buffer.getvalue()


def gerar_pdf_resumo(
    usuario: UsuarioLogado,
    inicio: date | None,
    fim: date | None,
) -> tuple[bytes, str]:
    inicio_final, fim_final = _resolver_periodo(inicio, fim)
    dados = insights_service.obter_insights(usuario, inicio_final, fim_final)
    if dados.get("versao_contrato") != "insights-v2":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Contrato de Insights incompatível com o relatório gerencial.",
        )
    nome = f"sgnc-resumo-{inicio_final.isoformat()}-{fim_final.isoformat()}.pdf"
    return _montar_pdf(dados, usuario), nome
