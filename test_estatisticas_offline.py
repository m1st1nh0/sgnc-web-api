"""Regressão do endpoint individual quando há NC dentro da janela de 12 meses."""
from datetime import date

from app import estatisticas_service_v2 as estatisticas
from app.auth import UsuarioLogado


class Resp:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, _campos):
        return self

    def eq(self, campo, valor):
        self.rows = [row for row in self.rows if row.get(campo) == valor]
        return self

    def in_(self, campo, valores):
        self.rows = [row for row in self.rows if row.get(campo) in valores]
        return self

    def gte(self, campo, valor):
        self.rows = [row for row in self.rows if row.get(campo) >= valor]
        return self

    def lte(self, campo, valor):
        self.rows = [row for row in self.rows if row.get(campo) <= valor]
        return self

    def order(self, campo, desc=False):
        self.rows.sort(key=lambda row: row.get(campo) or "", reverse=desc)
        return self

    def execute(self):
        return Resp(list(self.rows))


class FakeServico:
    def table(self, nome):
        if nome == "usuarios":
            return Query([
                {
                    "id": "func-1",
                    "nome": "Colaborador",
                    "setor": "Operação",
                    "papel": "funcionario",
                    "supervisor_id": "sup-1",
                }
            ])
        if nome == "nao_conformidades":
            return Query([
                {
                    "id": 7,
                    "colaborador_id": "func-1",
                    "status": "aguardando_feedback",
                    "data": date.today().isoformat(),
                }
            ])
        if nome == "nc_causas":
            return Query([
                {
                    "nc_id": 7,
                    "causa_id": 10,
                    "ocorrencia_numero": 1,
                    "causas": {"descricao": "Cadastro"},
                }
            ])
        if nome == "medidas_disciplinares":
            return Query([])
        raise AssertionError(f"Tabela inesperada: {nome}")


estatisticas.cliente_servico = lambda: FakeServico()
funcionario = UsuarioLogado(
    "func-1", "Colaborador", "func@example.com", "funcionario", False, "fake"
)

saida = estatisticas.obter_estatisticas_colaborador(funcionario, "func-1")

assert saida["total_nc_12m"] == 1
assert saida["causas"] == [
    {
        "causa_id": 10,
        "causa": "Cadastro",
        "ocorrencias_12m": 1,
        "ultima_ocorrencia_numero": 1,
        "ultima_ocorrencia_nc_id": 7,
        "medida_sugerida": None,
        "medidas": [],
    }
]

print("ESTATISTICAS OFFLINE TESTS PASSED")
