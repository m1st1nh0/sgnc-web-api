from datetime import date
from pathlib import Path

from fastapi import HTTPException

from app.auth import UsuarioLogado
from app import relatorios_service as rel


usuario = UsuarioLogado(
    id="supervisor-1",
    nome="Supervisor Teste",
    email="supervisor@example.com",
    papel="supervisor",
    senha_provisoria=False,
    token="offline",
)


# Período padrão usa 12 meses de calendário, inclusive em ano bissexto.
inicio, fim = rel._resolver_periodo(None, date(2024, 2, 29))
assert inicio == date(2023, 2, 28)
assert fim == date(2024, 2, 29)

try:
    rel._resolver_periodo(date(2026, 8, 2), date(2026, 8, 1))
    raise AssertionError("período invertido deveria falhar")
except HTTPException as exc:
    assert exc.status_code == 400

try:
    rel._validar_status("qualquer")
    raise AssertionError("status inválido deveria falhar")
except HTTPException as exc:
    assert exc.status_code == 400


ncs = [
    {
        "id": 10,
        "data": "2026-08-20",
        "status": "validada",  # legado deve responder ao filtro canônico feedback
        "colaborador_id": "colab-1",
        "colaborador": "Ana",
        "setor": "Suporte",
        "criticidade": "alta",
        "chamado": "CH-10",
        "descricao": "Falha no cadastro",
        "criado_em": "2026-08-20T10:00:00+00:00",
        "validado_em": "2026-08-20T11:00:00+00:00",
        "feedback_aplicado_em": None,
        "aceito_em": None,
    },
    {
        "id": 11,
        "data": "2026-07-10",
        "status": "concluida",
        "colaborador_id": "colab-2",
        "colaborador": "Bruno",
        "setor": "Financeiro",
        "criticidade": "media",
        "chamado": "",
        "descricao": "Outro caso",
        "criado_em": "2026-07-10T10:00:00+00:00",
        "validado_em": "2026-07-10T11:00:00+00:00",
        "feedback_aplicado_em": "2026-07-10T12:00:00+00:00",
        "aceito_em": "2026-07-10T13:00:00+00:00",
    },
]

filtradas = rel._filtrar_ncs(
    ncs,
    date(2026, 8, 1),
    date(2026, 8, 31),
    status_filtro="aguardando_feedback",
    colaborador_id="colab-1",
    setor=" suporte ",
)
assert [nc["id"] for nc in filtradas] == [10]
assert rel._normalizar_status(filtradas[0]["status"]) == "aguardando_feedback"


causas = {
    10: [
        {
            "causa_id": 1,
            "descricao": "Erro; cadastro",
            "ocorrencia_numero": 2,
        }
    ]
}
csv_bytes = rel._montar_csv([ncs[0]], causas)
assert csv_bytes.startswith(b"\xef\xbb\xbf")
texto_csv = csv_bytes.decode("utf-8-sig")
assert "Reincidente 12m" in texto_csv
assert "Sim" in texto_csv
assert "Erro; cadastro (#2)" in texto_csv
assert "aguardando_feedback" in texto_csv
assert "1,00" in texto_csv  # uma hora até a validação


sample_insights = {
    "versao_contrato": "insights-v2",
    "periodo": {"inicio": "2026-01-01", "fim": "2026-08-29"},
    "escopo": {"tipo": "equipe_direta", "quantidade_colaboradores": 2},
    "kpis": {
        "backlog_ativo_atual": 4,
        "abertas_atuais": 2,
        "aguardando_feedback_atual": 1,
        "aguardando_aceite_atual": 1,
        "total_ncs": 8,
        "concluidas_no_periodo": 3,
        "invalidadas_no_periodo": 1,
        "taxa_invalidacao": 0.125,
    },
    "aged_backlog": {
        "mais_antiga": {"nc_id": 7, "dias_na_etapa": 9},
    },
    "tempos": {
        "criacao_ate_validacao": {"mediana_horas": 1.5},
        "validacao_ate_feedback": {"mediana_horas": 2.0},
        "feedback_ate_aceite": {"mediana_horas": 3.0},
        "ciclo_total": {"mediana_horas": 7.0},
    },
    "ncs_por_causa": [
        {"causa": "Cadastro & integração", "total": 4, "total_reincidentes": 1}
    ],
    "disciplina": {
        "aplicadas": {"total": 1, "advertencias": 1, "suspensoes": 0},
        "sugeridas": {"total": 2},
    },
}
pdf = rel._montar_pdf(sample_insights, usuario)
assert pdf.startswith(b"%PDF")
assert len(pdf) > 1000



from app import relatorio_documentos as documentos

sample_nc = {
    "id": 14,
    "data": "2026-08-20",
    "status": "concluida",
    "criticidade": "alta",
    "chamado": "CH-14",
    "colaborador": "Ana",
    "setor": "Suporte",
    "reincidencia": "Sim",
    "descricao": "Imagem de teste",
    "causas": ["Erro de cadastro"],
    "feedback": "Corrigir o procedimento.",
    "criado_em": "2026-08-20T10:00:00+00:00",
    "atualizado_em": "2026-08-20T13:00:00+00:00",
}
# PNG 1x1 usado somente para garantir que imagens binárias entram no PDF.
png_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\x0dIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99\x9d"
    b"\x00\x00\x00\x00IEND\xaeB\x60\x82"
)
pdf_nc = documentos._montar_pdf_nc(
    sample_nc,
    [{"id": 1, "nome_original": "foto.png", "bytes": png_1x1}],
    [{"status_novo": "aberta", "criado_em": "2026-08-20T10:00:00+00:00", "observacao": "NC aberta"}],
)
assert pdf_nc.startswith(b"%PDF")
assert len(pdf_nc) > 1000

sample_dossie = {
    "nome": "Ana",
    "setor": "Suporte",
    "total_nc_12m": 2,
    "causas": [{
        "causa": "Erro de cadastro",
        "ocorrencias_12m": 2,
        "ultima_ocorrencia_nc_id": 14,
        "ultima_ocorrencia_numero": 2,
        "medida_sugerida": None,
        "medidas": [],
    }],
}
pdf_dossie = documentos._montar_pdf_dossie(sample_dossie, [sample_nc])
assert pdf_dossie.startswith(b"%PDF")
assert len(pdf_dossie) > 1000
\nservico_fonte = Path("app/relatorios_service.py").read_text(encoding="utf-8")
router_fonte = Path("app/routers/relatorios_router.py").read_text(encoding="utf-8")
main_fonte = Path("app/main.py").read_text(encoding="utf-8")

# Escopo de supervisor precisa vir da mesma regra consolidada dos Insights.
assert "pr01._ids_equipe_direta" in servico_fonte
assert '.in_("colaborador_id", equipe_ids)' in servico_fonte
assert "ocorrencia_numero" in servico_fonte
assert 'nc.get("reincidencia")' not in servico_fonte
assert "url_temporaria" not in servico_fonte

# Downloads são gestão-only e resistentes a content sniffing.
assert "Depends(exigir_gestao)" in router_fonte
assert 'media_type="text/csv; charset=utf-8"' in router_fonte
assert 'media_type="application/pdf"' in router_fonte
assert '"X-Content-Type-Options": "nosniff"' in router_fonte
assert "relatorios_router.router" in main_fonte\nassert "/usuarios/{usuario_id}/dossie.pdf" in router_fonte\nassert "/nc/{nc_id}.pdf" in router_fonte\nassert "exigir_senha_definitiva" in router_fonte\nassert "relatorio_documentos" in router_fonte\ndocumentos_fonte = Path("app/relatorio_documentos.py").read_text(encoding="utf-8")\nassert "storage.from_(BUCKET)" in documentos_fonte\nassert "bucket.download" in documentos_fonte\nassert "EVIDÊNCIAS ANEXADAS" in documentos_fonte

print("PR07 REPORT TESTS PASSED")
