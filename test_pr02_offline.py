"""Regressões offline da PR02: recorrência por causa e validação atômica."""
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from app.auth import UsuarioLogado
from app import nc_service as legacy
import app.nc_service_pr02 as nc_pr02
from app.recurrence_v2 import eh_reincidencia, inicio_janela_12_meses


# 12 meses de calendário, não aproximação de 365 dias.
assert inicio_janela_12_meses(date(2026, 8, 29)) == date(2025, 8, 29)
assert inicio_janela_12_meses(date(2024, 2, 29)) == date(2023, 2, 28)
assert not eh_reincidencia(None)
assert not eh_reincidencia(1)
assert eh_reincidencia(2)
assert eh_reincidencia(22)


# Preserva a política disciplinar existente; PR02 muda a contagem, não os
# gatilhos aprovados pelo domínio.
esperado = {
    1: None,
    3: None,
    4: "advertencia",
    5: None,
    7: "advertencia",
    10: "advertencia",
    13: "suspensao",
    16: "suspensao",
    19: "suspensao",
    22: "avaliar_justa_causa",
    25: "avaliar_justa_causa",
}
for numero, medida in esperado.items():
    assert legacy.decidir_medida_disciplina(numero) == medida, (numero, medida)


# O SQL precisa manter as garantias que tornam a regra segura sob concorrência.
sql = Path("docs/database/pr02_recurrence_v2.sql").read_text(encoding="utf-8")
sql_normalizado = " ".join(sql.lower().split())
assert "interval '12 months'" in sql
assert "pg_advisory_xact_lock" in sql
assert "hashtextextended" in sql
assert "order by rel.causa_id" in sql_normalizado
assert "security definer" in sql_normalizado
assert "from public, anon, authenticated" in sql_normalizado
assert "to service_role" in sql_normalizado
assert "ocorrencia_numero" in sql
assert "timedelta(days=365)" not in sql


# A camada de aplicação deve delegar a transição procedente para a RPC e
# apenas enriquecer o retorno com a sugestão disciplinar.
class Resp:
    def __init__(self, data):
        self.data = data


class RpcQuery:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return Resp(self.data)


class FakeServico:
    def __init__(self):
        self.chamadas = []

    def rpc(self, nome, params):
        self.chamadas.append((nome, params))
        return RpcQuery(
            {
                "ok": True,
                "nc_id": 42,
                "status": "aguardando_feedback",
                "reincidencia": "Sim",
                "ocorrencias": [
                    {"causa_id": 10, "ocorrencia_numero": 4},
                    {"causa_id": 20, "ocorrencia_numero": 1},
                ],
            }
        )


servico = FakeServico()
leituras = iter(
    [
        {
            "id": 42,
            "status": "aberta",
            "colaborador_id": "func-1",
            "data": "2026-08-29",
        },
        {
            "id": 42,
            "status": "aguardando_feedback",
            "colaborador_id": "func-1",
            "data": "2026-08-29",
            "reincidencia": "Sim",
        },
    ]
)

nc_pr02.base._servico = lambda: servico
nc_pr02.base._buscar_nc_servico = lambda _nc_id: next(leituras)
nc_pr02.legacy._registrar_historico = lambda *args, **kwargs: None
nc_pr02.legacy._montar_saida_nc = lambda _servico, nc: dict(nc)

adm = UsuarioLogado("adm-1", "ADM", "adm@x", "adm", False, "fake")
dados = SimpleNamespace(decisao="validar", motivo_invalidacao=None)
resposta = nc_pr02.avaliar_nc(adm, 42, dados)

assert servico.chamadas == [
    (
        "validar_nc_com_ocorrencias_v2",
        {"p_nc_id": 42, "p_responsavel_id": "adm-1"},
    )
]
assert resposta["status"] == "aguardando_feedback"
assert resposta["reincidencia"] == "Sim"
assert resposta["ocorrencias"] == [
    {
        "causa_id": 10,
        "ocorrencia_numero": 4,
        "medida_sugerida": "advertencia",
    },
    {
        "causa_id": 20,
        "ocorrencia_numero": 1,
        "medida_sugerida": None,
    },
]


# Erro de concorrência retornado pela RPC vira 409 na API.
class FakeServicoConflito(FakeServico):
    def rpc(self, nome, params):
        self.chamadas.append((nome, params))
        return RpcQuery({"ok": False, "erro": "nc_nao_aberta"})


from fastapi import HTTPException

servico_conflito = FakeServicoConflito()
nc_pr02.base._servico = lambda: servico_conflito
nc_pr02.base._buscar_nc_servico = lambda _nc_id: {
    "id": 43,
    "status": "aberta",
    "colaborador_id": "func-1",
}
try:
    nc_pr02.avaliar_nc(adm, 43, dados)
    raise AssertionError("concorrência deveria retornar HTTP 409")
except HTTPException as exc:
    assert exc.status_code == 409


# A regra antiga não pode voltar a ser chamada pelo serviço PR02.
fonte_servico = Path("app/nc_service_pr02.py").read_text(encoding="utf-8")
assert "calcular_reincidencia(" not in fonte_servico
assert "calcular_ocorrencias_da_nc(" not in fonte_servico

print("PR02 OFFLINE TESTS PASSED")
