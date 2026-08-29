"""Testes offline das regras centrais da PR01, sem rede/Supabase real."""
from datetime import date

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.auth import UsuarioLogado, exigir_gestao, exigir_senha_definitiva
import app.insights_service_v2 as insights
import app.usuario_service as usuarios


class Resp:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []

    def select(self, _fields):
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self.filters.append(("in", col, list(vals)))
        return self

    def order(self, _col, desc=False):
        return self

    def execute(self):
        rows = list(self.rows)
        for op, col, val in self.filters:
            if op == "eq":
                rows = [row for row in rows if row.get(col) == val]
            elif op == "in":
                rows = [row for row in rows if row.get(col) in val]
        return Resp(rows)


class FakeServico:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return Query(self.tables.get(name, []))


TABLES = {
    "usuarios": [
        {
            "id": "a",
            "nome": "Ana",
            "setor": "A",
            "ativo": True,
            "supervisor_id": "sup1",
        },
        {
            "id": "b",
            "nome": "Bia",
            "setor": "B",
            "ativo": True,
            "supervisor_id": "sup1",
        },
        {
            "id": "c",
            "nome": "Carlos",
            "setor": "C",
            "ativo": True,
            "supervisor_id": "sup2",
        },
        {
            "id": "d",
            "nome": "Desativado",
            "setor": "D",
            "ativo": False,
            "supervisor_id": "sup2",
        },
    ],
    "nao_conformidades": [
        {
            "id": 1,
            "data": "2026-06-01",
            "status": "aguardando_feedback",
            "colaborador_id": "a",
            "colaborador": "Ana",
            "setor": "A",
            "criticidade": "Alta",
            "chamado": "1",
            "reincidencia": "Não",
            "aceito_em": None,
            "atualizado_em": None,
            "criado_em": "2026-06-01T00:00:00+00:00",
        },
        {
            "id": 2,
            "data": "2026-07-01",
            "status": "concluida",
            "colaborador_id": "b",
            "colaborador": "Bia",
            "setor": "B",
            "criticidade": "Baixa",
            "chamado": None,
            "reincidencia": "Sim",
            "aceito_em": "2026-07-05T00:00:00+00:00",
            "atualizado_em": None,
            "criado_em": "2026-07-01T00:00:00+00:00",
        },
        {
            "id": 3,
            "data": "2026-08-01",
            "status": "concluida",
            "colaborador_id": "c",
            "colaborador": "Carlos",
            "setor": "C",
            "criticidade": "Alta",
            "chamado": "3",
            "reincidencia": "Sim",
            "aceito_em": "2026-08-05T00:00:00+00:00",
            "atualizado_em": None,
            "criado_em": "2026-08-01T00:00:00+00:00",
        },
    ],
    "nc_causas": [
        {"nc_id": 1, "causa_id": 10, "causas": {"descricao": "Processo"}},
        {"nc_id": 2, "causa_id": 10, "causas": {"descricao": "Processo"}},
        {"nc_id": 3, "causa_id": 20, "causas": {"descricao": "Treinamento"}},
    ],
    "causas": [
        {"id": 10, "descricao": "Processo"},
        {"id": 20, "descricao": "Treinamento"},
    ],
    "medidas_disciplinares": [
        {
            "causa_id": 10,
            "colaborador_id": "a",
            "tipo": "advertencia",
            "data_aplicacao": "2026-06-10",
        },
        {
            "causa_id": 20,
            "colaborador_id": "c",
            "tipo": "suspensao",
            "data_aplicacao": "2026-08-10",
        },
    ],
}

insights.cliente_servico = lambda: FakeServico(TABLES)

sup1 = UsuarioLogado("sup1", "Sup 1", "sup1@x", "supervisor", False, "fake")
resultado = insights.obter_insights(sup1, date(2026, 1, 1), date(2026, 12, 31))

assert resultado["escopo"] == {
    "tipo": "equipe_direta",
    "quantidade_colaboradores": 2,
}
assert resultado["kpis"]["total_ncs"] == 2, resultado["kpis"]
assert {x["colaborador"] for x in resultado["ncs_por_colaborador"]} == {
    "Ana",
    "Bia",
}
assert all(x["causa_id"] != 20 for x in resultado["ncs_por_causa"])
assert all(x["causa_id"] != 20 for x in resultado["medidas_por_causa"])

adm = UsuarioLogado("adm", "ADM", "adm@x", "adm", False, "fake")
resultado_adm = insights.obter_insights(
    adm, date(2026, 1, 1), date(2026, 12, 31)
)
assert resultado_adm["escopo"]["tipo"] == "global"
assert resultado_adm["kpis"]["total_ncs"] == 3

try:
    exigir_senha_definitiva(
        UsuarioLogado("u", "U", "u@x", "funcionario", True, "fake")
    )
    raise AssertionError("senha provisória deveria bloquear a operação")
except HTTPException as exc:
    assert exc.status_code == 403

liberado = exigir_senha_definitiva(
    UsuarioLogado("u", "U", "u@x", "funcionario", False, "fake")
)
assert liberado.id == "u"

# Qualquer usuário autenticado pode selecionar qualquer colaborador ativo
# para abrir uma NC. O endpoint permanece mínimo e não expõe dados de gestão.
usuarios.cliente_servico = lambda: FakeServico(TABLES)
funcionario = UsuarioLogado(
    "a", "Ana", "ana@x", "funcionario", False, "fake"
)
opcoes_nc = usuarios.listar_opcoes_nc(funcionario)
assert {item["id"] for item in opcoes_nc} == {"a", "b", "c"}
assert all(set(item) == {"id", "nome", "setor"} for item in opcoes_nc)
assert all(item["id"] != "d" for item in opcoes_nc)

# A rota HTTP usa exatamente o mesmo serviço V2 mockado, sem tocar na rede.
from app.main import app

app.dependency_overrides[exigir_gestao] = lambda: sup1
client = TestClient(app)
resposta = client.get("/insights?inicio=2026-01-01&fim=2026-12-31")
assert resposta.status_code == 200, resposta.text
dados_http = resposta.json()
assert dados_http["escopo"] == {
    "tipo": "equipe_direta",
    "quantidade_colaboradores": 2,
}
assert dados_http["kpis"]["total_ncs"] == 2

app.dependency_overrides.clear()

print("PR01 OFFLINE TESTS PASSED")
