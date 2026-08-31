"""Testes offline do contrato de onboarding híbrido."""
from pathlib import Path

from fastapi import HTTPException

from app.auth import UsuarioLogado
from app.onboarding_manifesto import (
    PAPEIS_ONBOARDING,
    VERSAO_ONBOARDING,
    chaves_por_origem,
    manifesto_papel,
    totais_papel,
)
from app import onboarding_service


assert VERSAO_ONBOARDING == 1
assert set(PAPEIS_ONBOARDING) == {"adm", "supervisor", "funcionario"}

for papel in PAPEIS_ONBOARDING:
    manifesto = manifesto_papel(papel)
    totais = totais_papel(papel)
    assert len(manifesto) == totais["total"]
    assert totais["apresentacao"] >= 4
    assert totais["checklist"] >= 7
    assert totais["contextual"] >= 5
    assert "apresentacao_boas_vindas" in manifesto
    assert "checklist_baixar_pdf" in manifesto
    assert "dica_nc_pdf" in manifesto
    assert len(chaves_por_origem(papel, "apresentacao")) == totais["apresentacao"]

try:
    manifesto_papel("papel-inexistente")
    raise AssertionError("papel inválido deveria falhar")
except ValueError:
    pass

usuario = UsuarioLogado(
    id="usuario-teste",
    nome="Usuário Teste",
    email="teste@example.com",
    papel="funcionario",
    senha_provisoria=False,
    token="offline",
)

# As validações acontecem antes de qualquer acesso ao banco.
try:
    onboarding_service.concluir_etapa(
        usuario, "etapa_inexistente", "checklist", {}
    )
    raise AssertionError("etapa inválida deveria falhar")
except HTTPException as exc:
    assert exc.status_code == 404

try:
    onboarding_service.concluir_etapa(
        usuario, "checklist_baixar_pdf", "contextual", {}
    )
    raise AssertionError("origem divergente deveria falhar")
except HTTPException as exc:
    assert exc.status_code == 400

router = Path("app/routers/onboarding_router.py").read_text(encoding="utf-8")
service = Path("app/onboarding_service.py").read_text(encoding="utf-8")
main = Path("app/main.py").read_text(encoding="utf-8")

assert 'prefix="/onboarding"' in router
assert router.count("Depends(exigir_senha_definitiva)") == 7
assert "/me/etapas/{chave_etapa}/concluir" in router
assert "onboarding_router.router" in main
assert '"usuario_id": usuario.id' in service
assert "on_conflict=" in service
assert "service_role" not in service

print("ONBOARDING HYBRID API TESTS PASSED")
