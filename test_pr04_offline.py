"""Regressões offline da PR04: contrato, escopo, tempos e aging."""
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.auth import UsuarioLogado
import app.insights_service_pr04 as insights


# ---------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------
assert insights._normalizar_status("validada") == "aguardando_feedback"
assert insights._normalizar_status("aguardando_analise") == "aguardando_feedback"
assert insights._normalizar_status("concluida") == "concluida"
assert insights._subtrair_um_ano(date(2024, 2, 29)) == date(2023, 2, 28)

resumo = insights._resumo_tempos([3600, 7200, 10800])
assert resumo["amostras"] == 3
assert resumo["media_segundos"] == 7200
assert resumo["mediana_segundos"] == 7200
assert resumo["media_horas"] == 2.0

agora = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
aging = insights._aged_backlog(
    [
        {
            "id": 1,
            "status": "aberta",
            "criado_em": "2026-08-10T00:00:00Z",
        },
        {
            "id": 2,
            "status": "aguardando_feedback",
            "validado_em": "2026-08-07T12:00:00Z",
        },
        {
            "id": 3,
            "status": "aguardando_aceite",
            "feedback_aplicado_em": "2026-08-01T12:00:00Z",
        },
    ],
    agora,
)
assert aging["total"] == 3
assert {item["faixa"]: item["quantidade"] for item in aging["faixas"]} == {
    "0-1d": 1,
    "2-3d": 1,
    "4-7d": 0,
    "8+d": 1,
}
assert aging["mais_antiga"]["nc_id"] == 3


# ---------------------------------------------------------
# Serviço completo com banco fake
# ---------------------------------------------------------
class Resp:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, _campos):
        return self

    def eq(self, campo, valor):
        self.rows = [linha for linha in self.rows if linha.get(campo) == valor]
        return self

    def in_(self, campo, valores):
        valores = set(valores)
        self.rows = [linha for linha in self.rows if linha.get(campo) in valores]
        return self

    def execute(self):
        return Resp(list(self.rows))


USUARIOS = [
    {"id": "u1", "supervisor_id": "sup"},
    {"id": "u2", "supervisor_id": "outro"},
]

NCS = [
    {
        "id": 1,
        "data": "2026-08-01",
        "status": "aberta",
        "colaborador_id": "u1",
        "colaborador": "Um",
        "setor": "A",
        "criticidade": "Baixa",
        "chamado": "1",
        "reincidencia": "Não",
        "criado_em": "2026-08-01T10:00:00Z",
        "atualizado_em": "2026-08-01T10:00:00Z",
        "validado_em": None,
        "feedback_aplicado_em": None,
        "aceito_em": None,
        "decidido_em": None,
        "enviado_em": None,
    },
    {
        "id": 2,
        "data": "2026-08-02",
        "status": "aguardando_feedback",
        "colaborador_id": "u1",
        "colaborador": "Um",
        "setor": "A",
        "criticidade": "Média",
        "chamado": "2",
        "reincidencia": "Não",
        "criado_em": "2026-08-02T10:00:00Z",
        "atualizado_em": "2026-08-03T10:00:00Z",
        "validado_em": "2026-08-03T10:00:00Z",
        "feedback_aplicado_em": None,
        "aceito_em": None,
        "decidido_em": "2026-08-03T10:00:00Z",
        "enviado_em": "2026-08-03T10:00:00Z",
    },
    {
        "id": 3,
        "data": "2026-08-04",
        "status": "aguardando_aceite",
        "colaborador_id": "u1",
        "colaborador": "Um",
        "setor": "A",
        "criticidade": "Média",
        "chamado": "3",
        "reincidencia": "Não",
        "criado_em": "2026-08-04T10:00:00Z",
        "atualizado_em": "2026-08-06T10:00:00Z",
        "validado_em": "2026-08-05T10:00:00Z",
        "feedback_aplicado_em": "2026-08-06T10:00:00Z",
        "aceito_em": None,
        "decidido_em": "2026-08-05T10:00:00Z",
        "enviado_em": "2026-08-05T10:00:00Z",
    },
    {
        "id": 4,
        "data": "2026-08-07",
        "status": "concluida",
        "colaborador_id": "u1",
        "colaborador": "Um",
        "setor": "A",
        "criticidade": "Alta",
        "chamado": "4",
        "reincidencia": "Sim",
        "criado_em": "2026-08-07T10:00:00Z",
        "atualizado_em": "2026-08-10T10:00:00Z",
        "validado_em": "2026-08-08T10:00:00Z",
        "feedback_aplicado_em": "2026-08-09T10:00:00Z",
        "aceito_em": "2026-08-10T10:00:00Z",
        "decidido_em": "2026-08-08T10:00:00Z",
        "enviado_em": "2026-08-08T10:00:00Z",
    },
    {
        "id": 5,
        "data": "2026-08-11",
        "status": "invalidada",
        "colaborador_id": "u1",
        "colaborador": "Um",
        "setor": "A",
        "criticidade": "Baixa",
        "chamado": "5",
        "reincidencia": "Não",
        "criado_em": "2026-08-11T10:00:00Z",
        "atualizado_em": "2026-08-12T10:00:00Z",
        "validado_em": None,
        "feedback_aplicado_em": None,
        "aceito_em": None,
        "decidido_em": "2026-08-12T10:00:00Z",
        "enviado_em": None,
    },
    {
        "id": 6,
        "data": "2026-08-07",
        "status": "concluida",
        "colaborador_id": "u2",
        "colaborador": "Dois",
        "setor": "B",
        "criticidade": "Alta",
        "chamado": "6",
        "reincidencia": "Sim",
        "criado_em": "2026-08-07T10:00:00Z",
        "atualizado_em": "2026-08-10T10:00:00Z",
        "validado_em": "2026-08-08T10:00:00Z",
        "feedback_aplicado_em": "2026-08-09T10:00:00Z",
        "aceito_em": "2026-08-10T10:00:00Z",
        "decidido_em": "2026-08-08T10:00:00Z",
        "enviado_em": "2026-08-08T10:00:00Z",
    },
]

NC_CAUSAS = [
    {"nc_id": 1, "causa_id": 10, "ocorrencia_numero": None, "causas": {"descricao": "Cadastro"}},
    {"nc_id": 2, "causa_id": 10, "ocorrencia_numero": 1, "causas": {"descricao": "Cadastro"}},
    {"nc_id": 3, "causa_id": 10, "ocorrencia_numero": 2, "causas": {"descricao": "Cadastro"}},
    {"nc_id": 4, "causa_id": 10, "ocorrencia_numero": 4, "causas": {"descricao": "Cadastro"}},
    {"nc_id": 5, "causa_id": 11, "ocorrencia_numero": None, "causas": {"descricao": "Prazo"}},
    {"nc_id": 6, "causa_id": 99, "ocorrencia_numero": 8, "causas": {"descricao": "Fora da equipe"}},
]

MEDIDAS = [
    {
        "causa_id": 10,
        "colaborador_id": "u1",
        "nc_id": 4,
        "ocorrencia_gatilho": 4,
        "tipo": "advertencia",
        "status": "aplicada",
        "data_aplicacao": "2026-08-10",
        "criado_em": "2026-08-10T10:00:00Z",
    },
    {
        "causa_id": 99,
        "colaborador_id": "u2",
        "nc_id": 6,
        "ocorrencia_gatilho": 8,
        "tipo": "suspensao",
        "status": "aplicada",
        "data_aplicacao": "2026-08-10",
        "criado_em": "2026-08-10T10:00:00Z",
    },
]


class FakeServico:
    def table(self, nome):
        if nome == "usuarios":
            return Query(USUARIOS)
        if nome == "nao_conformidades":
            return Query(NCS)
        if nome == "nc_causas":
            return Query(NC_CAUSAS)
        if nome == "medidas_disciplinares":
            return Query(MEDIDAS)
        if nome == "causas":
            return Query([])
        raise AssertionError(f"Tabela inesperada: {nome}")


insights.cliente_servico = lambda: FakeServico()
supervisor = UsuarioLogado("sup", "Supervisor", "s@x", "supervisor", False, "fake")
saida = insights.obter_insights(supervisor, date(2026, 8, 1), date(2026, 8, 31))

assert saida["versao_contrato"] == "insights-v2"
assert saida["escopo"] == {"tipo": "equipe_direta", "quantidade_colaboradores": 1}
assert saida["kpis"]["total_ncs"] == 5
assert saida["kpis"]["backlog_ativo_atual"] == 3
assert saida["kpis"]["abertas_atuais"] == 1
assert saida["kpis"]["aguardando_feedback_atual"] == 1
assert saida["kpis"]["aguardando_aceite_atual"] == 1
assert saida["kpis"]["concluidas_no_periodo"] == 1
assert saida["kpis"]["invalidadas_no_periodo"] == 1
assert saida["tempos"]["criacao_ate_validacao"]["amostras"] == 3
assert saida["tempos"]["validacao_ate_feedback"]["amostras"] == 2
assert saida["tempos"]["feedback_ate_aceite"]["amostras"] == 1
assert saida["tempos"]["ciclo_total"]["amostras"] == 1
assert saida["reincidencia_por_causa"][0]["causa_id"] == 10
assert saida["reincidencia_por_causa"][0]["reincidencias_12m"] == 2
assert saida["disciplina"]["aplicadas"]["advertencias"] == 1
assert saida["disciplina"]["aplicadas"]["suspensoes"] == 0
assert saida["disciplina"]["sugeridas"]["advertencias"] == 1
assert all(item.get("colaborador_id") != "u2" for item in saida["ncs_por_colaborador"])
assert all(item.get("causa_id") != 99 for item in saida["reincidencia_por_causa"])

funcionario = UsuarioLogado("u1", "Um", "u@x", "funcionario", False, "fake")
try:
    insights.obter_insights(funcionario, date(2026, 8, 1), date(2026, 8, 31))
except HTTPException as exc:
    assert exc.status_code == 403
else:
    raise AssertionError("Funcionário não pode acessar Insights gerenciais")


# ---------------------------------------------------------
# Router e contrato estático
# ---------------------------------------------------------
fonte_router = Path("app/routers/insights_router.py").read_text(encoding="utf-8")
assert "insights_service_pr04" in fonte_router

fonte = Path("app/insights_service_pr04.py").read_text(encoding="utf-8")
assert '"backlog_ativo_atual"' in fonte
assert '"mediana_segundos"' in fonte
assert '"reincidencias_12m"' in fonte
assert '"reincidiu_apos_conclusao"' in fonte  # alias temporário de compatibilidade
assert "nc.get(\"reincidencia\") == \"Sim\"" not in fonte

print("PR04 OFFLINE TESTS PASSED")
