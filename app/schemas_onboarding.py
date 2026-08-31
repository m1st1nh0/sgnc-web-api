"""Schemas da API de onboarding híbrido."""
from typing import Any, Literal

from pydantic import BaseModel, Field


class OnboardingEtapaEntrada(BaseModel):
    origem: Literal["apresentacao", "checklist", "contextual"]
    metadados: dict[str, Any] = Field(default_factory=dict)


class OnboardingEtapaSaida(BaseModel):
    chave: str
    origem: str
    concluida_em: str | None = None


class OnboardingSaida(BaseModel):
    execucao_id: str
    papel: str
    versao: int
    status: str
    iniciado_em: str | None = None
    concluido_em: str | None = None
    dispensado_em: str | None = None
    etapas_concluidas: list[OnboardingEtapaSaida]
    totais: dict[str, int]
    progresso: dict[str, int]
    deve_exibir_apresentacao: bool
    modo_revisao: bool = False
