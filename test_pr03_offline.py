"""Regressões offline da PR03: atomicidade contratual e durações."""
from datetime import datetime, timezone
from pathlib import Path

from app.auth import UsuarioLogado
from app.timeline_service import calcular_duracoes


# ---------------------------------------------------------
# Durações canônicas
# ---------------------------------------------------------
nc_concluida = {
    "status": "concluida",
    "criado_em": "2026-08-29T10:00:00+00:00",
    "validado_em": "2026-08-29T11:00:00+00:00",
    "feedback_aplicado_em": "2026-08-29T12:30:00+00:00",
    "aceito_em": "2026-08-29T13:00:00+00:00",
    "decidido_em": "2026-08-29T11:00:00+00:00",
}
d = calcular_duracoes(nc_concluida, datetime(2026, 8, 29, 14, tzinfo=timezone.utc))
assert d["criacao_ate_validacao_segundos"] == 3600
assert d["validacao_ate_feedback_segundos"] == 5400
assert d["feedback_ate_aceite_segundos"] == 1800
assert d["ciclo_total_segundos"] == 10800
assert d["criacao_ate_decisao_segundos"] == 3600
assert d["etapa_atual"] is None
assert d["tempo_etapa_atual_segundos"] is None

nc_pendente = {
    "status": "aguardando_feedback",
    "criado_em": "2026-08-29T10:00:00Z",
    "validado_em": "2026-08-29T11:00:00Z",
}
d2 = calcular_duracoes(nc_pendente, datetime(2026, 8, 29, 14, tzinfo=timezone.utc))
assert d2["etapa_atual"] == "aguardando_feedback"
assert d2["tempo_etapa_atual_segundos"] == 10800
assert d2["ciclo_total_segundos"] is None

# Timestamp invertido não pode gerar duração negativa.
nc_inconsistente = {
    "status": "concluida",
    "criado_em": "2026-08-29T12:00:00Z",
    "validado_em": "2026-08-29T11:00:00Z",
}
assert calcular_duracoes(nc_inconsistente)["criacao_ate_validacao_segundos"] is None


# ---------------------------------------------------------
# Contrato SQL: atomicidade e segurança
# ---------------------------------------------------------
sql = Path("docs/database/pr03_atomic_workflow_timeline.sql").read_text(encoding="utf-8")
sql_normalizado = " ".join(sql.lower().split())

for funcao in [
    "criar_nc_com_historico_v3",
    "validar_nc_com_workflow_v3",
    "invalidar_nc_v3",
    "enviar_nc_legada_v3",
    "aplicar_feedback_nc_v3",
    "aceitar_nc_v3",
]:
    assert f"function public.{funcao}" in sql_normalizado

# A validação V3 deve reutilizar a regra V2, não copiá-la.
assert "validar_nc_com_ocorrencias_v2" in sql
assert "pg_advisory_xact_lock" not in sql  # pertence somente à PR02

# Cada operação de fluxo grava auditoria dentro da própria função.
assert sql_normalizado.count("insert into public.historico_nc") >= 6

# RPCs novas preferem invoker e ficam fechadas para navegador.
assert sql_normalizado.count("security invoker") >= 6
assert "from public, anon, authenticated" in sql_normalizado
assert "to service_role" in sql_normalizado

# Regressão do erro encontrado no rollout do PR02.
assert "pg_catalog.coalesce" not in sql.lower()
assert "current_date()" not in sql.lower()


# ---------------------------------------------------------
# Serviço: não pode voltar a fazer segundo write de histórico
# ---------------------------------------------------------
fonte = Path("app/nc_service_pr03.py").read_text(encoding="utf-8")
assert "_registrar_historico" not in fonte
for rpc in [
    "criar_nc_com_historico_v3",
    "validar_nc_com_workflow_v3",
    "invalidar_nc_v3",
    "enviar_nc_legada_v3",
    "aplicar_feedback_nc_v3",
    "aceitar_nc_v3",
]:
    assert rpc in fonte


# ---------------------------------------------------------
# Timeline HTTP deve estar registrada no app
# ---------------------------------------------------------
from app.main import app

assert any(
    getattr(route, "path", None) == "/nc/{nc_id}/timeline"
    and "GET" in getattr(route, "methods", set())
    for route in app.routes
)


# ---------------------------------------------------------
# Redação das observações para autor sem visão completa
# ---------------------------------------------------------
import app.timeline_service as timeline


class Resp:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, _campos):
        return self

    def eq(self, _campo, _valor):
        return self

    def order(self, _campo):
        return self

    def execute(self):
        return Resp(list(self.rows))


class FakeServico:
    def table(self, _nome):
        return Query([
            {
                "id": 1,
                "usuario_id": "adm",
                "status_anterior": "aberta",
                "status_novo": "invalidada",
                "observacao": "motivo reservado",
                "criado_em": "2026-08-29T11:00:00Z",
            }
        ])


timeline.cliente_servico = lambda: FakeServico()
autor = UsuarioLogado("autor", "Autor", "a@x", "funcionario", False, "fake")
nc_autor = {
    "id": 99,
    "status": "invalidada",
    "aberto_por": "autor",
    "colaborador_id": "outra-pessoa",
    "responsavel_id": "adm",
    "criado_em": "2026-08-29T10:00:00Z",
    "decidido_em": "2026-08-29T11:00:00Z",
}
saida = timeline.obter_timeline(autor, nc_autor)
assert saida["eventos"][0]["observacao"] is None

adm = UsuarioLogado("adm", "ADM", "adm@x", "adm", False, "fake")
saida_adm = timeline.obter_timeline(adm, {**nc_autor, "aberto_por": "autor"})
assert saida_adm["eventos"][0]["observacao"] == "motivo reservado"

print("PR03 OFFLINE TESTS PASSED")
