"""
Teste offline (sem rede) da lógica de agregacao de insights, usando um
cliente Supabase fake em memória. Não faz parte da aplicação.
"""
from datetime import date

from app.auth import UsuarioLogado
import app.insights_service as svc


class FakeResposta:
    def __init__(self, dados):
        self.data = dados


class FakeConsulta:
    def __init__(self, linhas):
        self.linhas = linhas
        self._filtros = []

    def select(self, _selecao):
        return self

    def eq(self, col, val):
        self._filtros.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filtros.append(("in", col, list(vals)))
        return self

    def execute(self):
        linhas = self.linhas
        for op, col, val in self._filtros:
            if op == "eq":
                linhas = [l for l in linhas if l.get(col) == val]
            elif op == "in":
                linhas = [l for l in linhas if l.get(col) in val]
        return FakeResposta(linhas)


class FakeServico:
    def __init__(self, tabelas):
        self.tabelas = tabelas

    def table(self, nome):
        return FakeConsulta(self.tabelas.get(nome, []))


NCS = [
    {   # NC1
        "id": 1, "data": "2026-07-01", "status": "aberta",
        "colaborador_id": "a", "colaborador": "Ana", "setor": "Produção",
        "criticidade": "Alta", "chamado": "CH123", "reincidencia": "Não",
        "aceito_em": None, "atualizado_em": None,
        "criado_em": "2026-07-01T00:00:00+00:00",
    },
    {   # NC2 concluída, sem chamado, reincidente
        "id": 2, "data": "2026-06-01", "status": "concluida",
        "colaborador_id": "a", "colaborador": "Ana", "setor": "Produção",
        "criticidade": "Média", "chamado": None, "reincidencia": "Sim",
        "aceito_em": "2026-06-20T10:00:00+00:00",
        "atualizado_em": None, "criado_em": "2026-06-01T00:00:00+00:00",
    },
    {   # NC3 concluída, outra causa
        "id": 3, "data": "2026-07-15", "status": "concluida",
        "colaborador_id": "a", "colaborador": "Ana", "setor": "Produção",
        "criticidade": "Baixa", "chamado": "CH999", "reincidencia": "Sim",
        "aceito_em": "2026-07-20T10:00:00+00:00",
        "atualizado_em": None, "criado_em": "2026-07-15T00:00:00+00:00",
    },
    {   # NC4 invalidada, sem chamado (string vazia)
        "id": 4, "data": "2026-08-01", "status": "invalidada",
        "colaborador_id": "b", "colaborador": "Bruno", "setor": "Logística",
        "criticidade": "Alta", "chamado": "", "reincidencia": "Não",
        "aceito_em": None, "atualizado_em": None,
        "criado_em": "2026-08-01T00:00:00+00:00",
    },
    {   # NC5 concluída FORA do período (referência de conclusão histórica)
        "id": 5, "data": "2025-01-10", "status": "concluida",
        "colaborador_id": "c", "colaborador": "Carol", "setor": "Administração",
        "criticidade": "Média", "chamado": "CH500", "reincidencia": "Sim",
        "aceito_em": "2025-02-05T10:00:00+00:00",
        "atualizado_em": None, "criado_em": "2025-01-10T00:00:00+00:00",
    },
    {   # NC6 validada, dentro do período, mesma causa/colab da NC5
        "id": 6, "data": "2026-03-01", "status": "validada",
        "colaborador_id": "c", "colaborador": "Carol", "setor": "Administração",
        "criticidade": "Baixa", "chamado": "CH601", "reincidencia": "Sim",
        "aceito_em": None, "atualizado_em": None,
        "criado_em": "2026-03-01T00:00:00+00:00",
    },
]

NC_CAUSAS = [
    {"nc_id": 1, "causa_id": 1, "causas": {"descricao": "Falha de processo"}},
    {"nc_id": 2, "causa_id": 1, "causas": {"descricao": "Falha de processo"}},
    {"nc_id": 3, "causa_id": 2, "causas": {"descricao": "Atraso de entrega"}},
    {"nc_id": 4, "causa_id": 1, "causas": {"descricao": "Falha de processo"}},
    {"nc_id": 5, "causa_id": 1, "causas": {"descricao": "Falha de processo"}},
    {"nc_id": 6, "causa_id": 1, "causas": {"descricao": "Falha de processo"}},
]

CAUSAS = [
    {"id": 1, "descricao": "Falha de processo"},
    {"id": 2, "descricao": "Atraso de entrega"},
]

MEDIDAS = [
    {"causa_id": 1, "tipo": "advertencia", "data_aplicacao": "2026-05-01"},
    {"causa_id": 1, "tipo": "suspensao", "data_aplicacao": "2026-05-02"},
    # fora do período -> deve ser desconsiderada
    {"causa_id": 2, "tipo": "avaliar_justa_causa", "data_aplicacao": "2027-01-01"},
    # legado sem data -> deve entrar
    {"causa_id": 1, "tipo": "advertencia", "data_aplicacao": None},
]

TABELAS = {
    "nao_conformidades": NCS,
    "nc_causas": NC_CAUSAS,
    "causas": CAUSAS,
    "medidas_disciplinares": MEDIDAS,
}

svc.cliente_servico = lambda: FakeServico(TABELAS)

usuario = UsuarioLogado(
    id="adm1", nome="ADM", email="adm@teste", papel="adm",
    senha_provisoria=False, token="fake",
)

resultado = svc.obter_insights(usuario, date(2026, 1, 1), date(2026, 12, 31))


def achar(lista, chave, valor):
    for item in lista:
        if item.get(chave) == valor:
            return item
    raise AssertionError(f"{chave}={valor} não encontrado em {lista}")


kpis = resultado["kpis"]
assert kpis["total_ncs"] == 5, kpis
assert kpis["ncs_abertas"] == 1, kpis
assert kpis["ncs_pendentes"] == 1, kpis        # NC6 validada
assert kpis["ncs_concluidas"] == 2, kpis        # NC2 e NC3
assert kpis["ncs_invalidadas"] == 1, kpis
assert kpis["taxa_invalidacao"] == 0.2, kpis
assert kpis["ncs_sem_chamado"] == 2, kpis       # NC2 (None) e NC4 ("")

meses = {m["mes"]: m for m in resultado["ncs_por_mes"]}
assert meses["2026-03"]["total"] == 1, meses
assert meses["2026-06"]["total"] == 1, meses
assert meses["2026-07"]["total"] == 2, meses
assert meses["2026-08"]["invalidadas"] == 1, meses
assert len(meses) == 12, len(meses)            # quadro completo, meses vazios com zero

status_validada = achar(resultado["ncs_por_status"], "status", "validada")
assert status_validada["quantidade"] == 1, resultado["ncs_por_status"]

ana = achar(resultado["ncs_por_colaborador"], "colaborador", "Ana")
assert ana["total"] == 3 and ana["reincidencias"] == 2, ana
bruno = achar(resultado["ncs_por_colaborador"], "colaborador", "Bruno")
assert bruno["invalidadas"] == 1, bruno

setor_log = achar(resultado["ncs_por_setor"], "setor", "Logística")
assert setor_log["total"] == 1 and setor_log["invalidadas"] == 1, setor_log

alta = achar(resultado["ncs_por_criticidade"], "criticidade", "Alta")
assert alta["total"] == 2, resultado["ncs_por_criticidade"]

causa1 = achar(resultado["ncs_por_causa"], "causa_id", 1)
assert causa1["total"] == 4 and causa1["total_reincidentes"] == 2, causa1

med_causa1 = achar(resultado["medidas_por_causa"], "causa_id", 1)
assert med_causa1["advertencias"] == 2, med_causa1
assert med_causa1["suspensoes"] == 1, med_causa1
assert med_causa1["total"] == 3, med_causa1
assert all(m["causa_id"] != 2 for m in resultado["medidas_por_causa"]), resultado["medidas_por_causa"]

rec_causa1 = achar(resultado["reincidencia_por_causa"], "causa_id", 1)
assert rec_causa1["ocorrencias"] == 2, rec_causa1       # NC2 (concluida) + NC6 (validada)
assert rec_causa1["reincidiu_apos_conclusao"] == 1, rec_causa1  # NC6 após NC5
rec_causa2 = achar(resultado["reincidencia_por_causa"], "causa_id", 2)
assert rec_causa2["ocorrencias"] == 1, rec_causa2
assert rec_causa2["reincidiu_apos_conclusao"] == 0, rec_causa2

# Colaborador sem permissão deve receber 403
from fastapi import HTTPException

try:
    svc.obter_insights(
        UsuarioLogado("f1", "Func", "func@teste", "funcionario", False, "fake"),
        date(2026, 1, 1), date(2026, 12, 31),
    )
    raise AssertionError("funcionario não deveria ter acesso")
except HTTPException as e:
    assert e.status_code == 403

print("TODOS OS TESTES PASSARAM ✓")

# ------------------- teste via HTTP (TestClient) -------------------
from fastapi.testclient import TestClient

from app.main import app
from app.auth import exigir_gestao

app.dependency_overrides[exigir_gestao] = lambda: UsuarioLogado(
    id="adm1", nome="ADM", email="adm@teste", papel="adm",
    senha_provisoria=False, token="fake",
)

client = TestClient(app)
resposta = client.get("/insights?inicio=2026-01-01&fim=2026-12-31")
assert resposta.status_code == 200, resposta.text
dados = resposta.json()
assert dados["kpis"]["total_ncs"] == 5, dados["kpis"]

# Sem parâmetros: a rota também responde (período padrão de 12 meses)
resposta_sem_params = client.get("/insights")
assert resposta_sem_params.status_code == 200, resposta_sem_params.text

# Funcionário (sem override) continuaria bloqueado pela dependência real;
# aqui garantimos que o JSON serializa corretamente e a rota responde.
print("TESTE VIA HTTP PASSARAM ✓")
print("kpis:", dados["kpis"])