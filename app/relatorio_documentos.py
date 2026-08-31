"""Documentos PDF individuais do SGNC (dossiê e relatório de NC)."""
from __future__ import annotations

from datetime import date, datetime
from html import escape
from io import BytesIO
from pathlib import Path
import re
import unicodedata

from fastapi import HTTPException, status
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image as PdfImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.auth import UsuarioLogado
from app.supabase_client import cliente_servico
from app import estatisticas_service_v2
from app import nc_service_pr03


NAVY = colors.HexColor("#17324D")
GREEN = colors.HexColor("#19A98A")
TEXT = colors.HexColor("#20364A")
MUTED = colors.HexColor("#64748B")
BORDER = colors.HexColor("#D7E0E5")
PALE = colors.HexColor("#F4F7F8")
ORANGE = colors.HexColor("#F28C28")
BUCKET = "evidencias"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _texto(value, fallback="-") -> str:
    if value is None or str(value).strip() == "":
        return fallback
    return str(value)


def _data(value) -> str:
    if value is None:
        return "-"
    try:
        if isinstance(value, datetime):
            return value.strftime("%d/%m/%Y")
        if isinstance(value, date):
            return value.strftime("%d/%m/%Y")
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return str(value)[:10]


def _data_hora(value) -> str:
    if value is None:
        return "-"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return str(value)


def _status_label(value) -> str:
    labels = {
        "aberta": "Aberta",
        "validada": "Aguardando feedback",
        "aguardando_analise": "Aguardando feedback",
        "aguardando_feedback": "Aguardando feedback",
        "aguardando_aceite": "Aguardando aceite",
        "concluida": "Concluída",
        "invalidada": "Invalidada",
    }
    return labels.get(value, _texto(value))


def _criticidade_label(value) -> str:
    return {"baixa": "Baixa", "media": "Média", "alta": "Alta", "critica": "Crítica"}.get(
        value, _texto(value)
    )


def _para(text, style) -> Paragraph:
    return Paragraph(escape(_texto(text)).replace("\n", "<br/>"), style)


def _card(title: str, body, width: float):
    title_style = ParagraphStyle(
        "card-title", fontName="Helvetica-Bold", fontSize=8.5, leading=10,
        textColor=NAVY, spaceAfter=3,
    )
    table = Table([[Paragraph(title.upper(), title_style)], [body]], colWidths=[width])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.65, BORDER),
        ("LINEBELOW", (0, 0), (-1, 0), 0.65, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        ("TOPPADDING", (0, 1), (-1, 1), 8),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table


def _kv_table(items: list[tuple[str, str]], width: float, styles) -> Table:
    rows = []
    for index in range(0, len(items), 2):
        pair = items[index:index + 2]
        row = []
        for label, value in pair:
            row.extend([
                Paragraph(escape(label.upper()), styles["label"]),
                Paragraph(escape(_texto(value)), styles["value"]),
            ])
        while len(row) < 4:
            row.extend([Paragraph("", styles["label"]), Paragraph("", styles["value"])])
        rows.append(row)
    table = Table(rows, colWidths=[width * .22, width * .28, width * .22, width * .28])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("body-sgnc", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=9, leading=13, textColor=TEXT))
    styles.add(ParagraphStyle("label", parent=styles["BodyText"], fontName="Helvetica-Bold",
        fontSize=7, leading=9, textColor=MUTED))
    styles.add(ParagraphStyle("value", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=9, leading=11, textColor=TEXT))
    styles.add(ParagraphStyle("section", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=10, leading=12, textColor=NAVY, spaceBefore=8, spaceAfter=5))
    styles.add(ParagraphStyle("small", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=7.5, leading=10, textColor=MUTED))
    styles.add(ParagraphStyle("timeline", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=8, leading=10, textColor=TEXT))
    styles.add(ParagraphStyle("pill", parent=styles["BodyText"], fontName="Helvetica-Bold",
        fontSize=8.5, leading=10, textColor=NAVY, alignment=1))
    return styles


def _cabecalho(canvas, doc, titulo: str, subtitulo: str):
    canvas.saveState()
    width, height = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, height - 17 * mm, width, 17 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(18 * mm, height - 10 * mm, "ARQUEM")
    canvas.setFillColor(GREEN)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(18 * mm, height - 14 * mm, "AUTOMAÇÃO CORPORATIVA")
    canvas.setFillColor(NAVY)
    canvas.setFont("Helvetica-Bold", 18)
    canvas.drawString(18 * mm, height - 31 * mm, titulo)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 9)
    canvas.drawString(18 * mm, height - 37 * mm, subtitulo)
    canvas.setStrokeColor(BORDER)
    canvas.line(18 * mm, height - 41 * mm, width - 18 * mm, height - 41 * mm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(width - 18 * mm, 10 * mm, f"SGNC  |  Página {doc.page}")
    canvas.restoreState()


def _doc(title: str, subtitle: str, buffer: BytesIO) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=46 * mm, bottomMargin=16 * mm, title=title, author="SGNC",
    )


def _evidencias(servico, nc_id: int) -> list[dict]:
    linhas = (
        servico.table("evidencias")
        .select("id, nome_original, caminho_storage, criado_em")
        .eq("nc_id", nc_id)
        .order("criado_em")
        .execute()
        .data
    )
    saida = []
    bucket = servico.storage.from_(BUCKET)
    for linha in linhas:
        item = dict(linha)
        try:
            item["bytes"] = bucket.download(linha["caminho_storage"])
        except Exception:
            item["bytes"] = None
        saida.append(item)
    return saida


def _historico(servico, nc_id: int) -> list[dict]:
    return (
        servico.table("historico_nc")
        .select("id, status_anterior, status_novo, observacao, criado_em")
        .eq("nc_id", nc_id)
        .order("criado_em")
        .execute()
        .data
    )


def _montar_pdf_nc(nc: dict, evidencias: list[dict], historico: list[dict]) -> bytes:
    styles = _styles()
    buffer = BytesIO()
    doc = _doc("Relatório de Não Conformidade", f"NC #{_texto(nc.get('id'))}", buffer)
    width = A4[0] - 36 * mm
    story = [
        Paragraph("Relatório de Não Conformidade", styles["Title"]),
        Paragraph(f"NC #{_texto(nc.get('id'))}  |  {_status_label(nc.get('status'))}", styles["small"]),
        Spacer(1, 4 * mm),
    ]
    resumo = Table([[
        _card("Status", Paragraph(_status_label(nc.get("status")), styles["pill"]), width / 4 - 3 * mm),
        _card("Criticidade", Paragraph(_criticidade_label(nc.get("criticidade")), styles["pill"]), width / 4 - 3 * mm),
        _card("Data da ocorrência", Paragraph(_data(nc.get("data")), styles["pill"]), width / 4 - 3 * mm),
        _card("Chamado", Paragraph(_texto(nc.get("chamado")), styles["pill"]), width / 4 - 3 * mm),
    ]], colWidths=[width / 4] * 4)
    resumo.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm)]))
    story.append(resumo)
    story.append(Paragraph("ANDAMENTO", styles["section"]))
    eventos = historico or [{"status_novo": nc.get("status"), "criado_em": nc.get("criado_em"), "observacao": "Registro atual"}]
    linhas = [[Paragraph("<b>Status</b>", styles["timeline"]), Paragraph("<b>Data</b>", styles["timeline"]),
               Paragraph("<b>Observação</b>", styles["timeline"])]]
    for evento in eventos:
        linhas.append([
            _para(_status_label(evento.get("status_novo")), styles["timeline"]),
            _para(_data_hora(evento.get("criado_em")), styles["timeline"]),
            _para(evento.get("observacao") or "-", styles["timeline"]),
        ])
    timeline = Table(linhas, colWidths=[42 * mm, 39 * mm, width - 81 * mm], repeatRows=1)
    timeline.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PALE), ("TEXTCOLOR", (0, 0), (-1, 0), NAVY),
        ("BOX", (0, 0), (-1, -1), .65, BORDER), ("INNERGRID", (0, 0), (-1, -1), .35, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(timeline)
    story.append(Paragraph("DADOS DA NÃO CONFORMIDADE", styles["section"]))
    dados = _kv_table([
        ("Colaborador", nc.get("colaborador")), ("Setor", nc.get("setor")),
        ("Reincidência", nc.get("reincidencia")), ("Responsável", nc.get("responsavel")),
        ("Criada em", _data_hora(nc.get("criado_em"))), ("Atualizada em", _data_hora(nc.get("atualizado_em"))),
    ], width, styles)
    story.append(_card("Dados", dados, width))
    story.append(Paragraph("DESCRIÇÃO", styles["section"]))
    story.append(_card("Descrição registrada", _para(nc.get("descricao"), styles["body-sgnc"]), width))
    story.append(Paragraph("CAUSAS", styles["section"]))
    causas = nc.get("causas") or []
    story.append(_card("Causas relacionadas", _para(" | ".join(map(str, causas)) if causas else "-", styles["body-sgnc"]), width))
    story.append(Paragraph("FEEDBACK REGISTRADO", styles["section"]))
    story.append(_card("Feedback", _para(nc.get("feedback"), styles["body-sgnc"]), width))
    story.append(Paragraph("EVIDÊNCIAS ANEXADAS", styles["section"]))
    evidence_flow = []
    image_items = []
    for evidence in evidencias:
        nome = _texto(evidence.get("nome_original"))
        suffix = Path(nome).suffix.lower()
        if suffix in IMAGE_EXTENSIONS and evidence.get("bytes"):
            try:
                image = PdfImage(BytesIO(evidence["bytes"]))
                image._restrictSize(72 * mm, 52 * mm)
                image_items.append([image, Paragraph(escape(nome), styles["small"])])
            except Exception:
                image_items.append([Paragraph(f"Imagem: {escape(nome)}", styles["body-sgnc"])])
        else:
            image_items.append([Paragraph(f"Arquivo anexado: {escape(nome)}", styles["body-sgnc"])])
    if image_items:
        for item in image_items:
            evidence_flow.append(item)
    else:
        evidence_flow.append([Paragraph("Nenhuma evidência anexada.", styles["body-sgnc"])])
    evidence_table = Table(evidence_flow, colWidths=[width], repeatRows=0)
    evidence_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), .65, BORDER), ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(evidence_table)
    doc.build(story, onFirstPage=lambda c, d: _cabecalho(c, d, "Relatório de Não Conformidade", f"NC #{_texto(nc.get('id'))}"),
              onLaterPages=lambda c, d: _cabecalho(c, d, "Relatório de Não Conformidade", f"NC #{_texto(nc.get('id'))}"))
    return buffer.getvalue()


def _montar_pdf_dossie(dados: dict, ncs: list[dict]) -> bytes:
    styles = _styles()
    buffer = BytesIO()
    nome = _texto(dados.get("nome"), "Colaborador")
    doc = _doc("Dossiê do Colaborador", nome, buffer)
    width = A4[0] - 36 * mm
    story = [
        Paragraph("Dossiê do Colaborador", styles["Title"]),
        Paragraph(f"{nome}  |  {_texto(dados.get('setor'))}", styles["small"]),
        Spacer(1, 5 * mm),
    ]
    kpi = Table([[
        _card("NCs nos últimos 12 meses", Paragraph(str(dados.get("total_nc_12m", 0)), styles["pill"]), width / 2 - 2 * mm),
        _card("Causas identificadas", Paragraph(str(len(dados.get("causas") or [])), styles["pill"]), width / 2 - 2 * mm),
    ]], colWidths=[width / 2] * 2)
    kpi.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm)]))
    story.append(kpi)
    story.append(Paragraph("RESUMO DO COLABORADOR", styles["section"]))
    story.append(_card("Identificação", _kv_table([
        ("Nome", dados.get("nome")), ("Setor", dados.get("setor")),
        ("Período analisado", "Últimos 12 meses"), ("Total de NCs", dados.get("total_nc_12m", 0)),
    ], width, styles), width))
    story.append(Paragraph("CAUSAS E RECORRÊNCIA", styles["section"]))
    causas = dados.get("causas") or []
    causa_rows = [[Paragraph("<b>Causa</b>", styles["timeline"]), Paragraph("<b>Ocorrências</b>", styles["timeline"]),
                    Paragraph("<b>Última ocorrência</b>", styles["timeline"]), Paragraph("<b>Medida sugerida</b>", styles["timeline"])]]
    for causa in causas:
        causa_rows.append([
            _para(causa.get("causa"), styles["timeline"]),
            _para(causa.get("ocorrencias_12m"), styles["timeline"]),
            _para(f"NC #{causa.get('ultima_ocorrencia_nc_id') or '-'} / ocorrência {causa.get('ultima_ocorrencia_numero') or '-'}", styles["timeline"]),
            _para(causa.get("medida_sugerida") or "Nenhuma", styles["timeline"]),
        ])
    if len(causa_rows) == 1:
        causa_rows.append([_para("Nenhuma causa registrada.", styles["timeline"]), "", "", ""])
    causas_table = Table(causa_rows, colWidths=[width * .38, width * .14, width * .24, width * .24], repeatRows=1)
    causas_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PALE), ("BOX", (0, 0), (-1, -1), .65, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), .35, BORDER), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(causas_table)
    story.append(Paragraph("HISTÓRICO RECENTE", styles["section"]))
    history_rows = [[Paragraph("<b>NC</b>", styles["timeline"]), Paragraph("<b>Data</b>", styles["timeline"]),
                     Paragraph("<b>Status</b>", styles["timeline"]), Paragraph("<b>Criticidade</b>", styles["timeline"])]]
    for nc in ncs[:12]:
        history_rows.append([
            _para(f"#{nc.get('id')}", styles["timeline"]),
            _para(_data(nc.get("data") or nc.get("criado_em")), styles["timeline"]),
            _para(_status_label(nc.get("status")), styles["timeline"]),
            _para(_criticidade_label(nc.get("criticidade")), styles["timeline"]),
        ])
    if len(history_rows) == 1:
        history_rows.append([_para("Nenhuma NC registrada.", styles["timeline"]), "", "", ""])
    history = Table(history_rows, colWidths=[width * .14, width * .24, width * .34, width * .28], repeatRows=1)
    history.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PALE), ("BOX", (0, 0), (-1, -1), .65, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), .35, BORDER), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(history)
    story.append(Paragraph("MEDIDAS DISCIPLINARES", styles["section"]))
    medidas = [medida for causa in causas for medida in (causa.get("medidas") or [])]
    medida_texto = " | ".join(
        f"{m.get('tipo', '-')} - ocorrência {m.get('ocorrencia_gatilho', '-')}" for m in medidas
    ) or "Nenhuma medida disciplinar registrada."
    story.append(_card("Registro", _para(medida_texto, styles["body-sgnc"]), width))
    doc.build(story, onFirstPage=lambda c, d: _cabecalho(c, d, "Dossiê do Colaborador", nome),
              onLaterPages=lambda c, d: _cabecalho(c, d, "Dossiê do Colaborador", nome))
    return buffer.getvalue()


def gerar_pdf_nc(usuario: UsuarioLogado, nc_id: int) -> tuple[bytes, str]:
    nc = nc_service_pr03.buscar_nc(usuario, nc_id)
    servico = cliente_servico()
    return _montar_pdf_nc(nc, _evidencias(servico, nc_id), _historico(servico, nc_id)), f"sgnc-nc-{nc_id}.pdf"


def gerar_pdf_dossie(usuario: UsuarioLogado, colaborador_id: str) -> tuple[bytes, str]:
    dados = estatisticas_service_v2.obter_estatisticas_colaborador(usuario, colaborador_id)
    servico = cliente_servico()
    ncs = (
        servico.table("nao_conformidades")
        .select("id, data, status, criticidade, criado_em, colaborador_id")
        .eq("colaborador_id", colaborador_id)
        .order("data", desc=True)
        .execute()
        .data
    )
    return _montar_pdf_dossie(dados, ncs), f"sgnc-dossie-{colaborador_id}.pdf"
