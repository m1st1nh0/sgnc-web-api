"""Rotas do onboarding híbrido do primeiro acesso."""
from fastapi import APIRouter, Depends

from app.auth import UsuarioLogado, exigir_senha_definitiva
from app.schemas_onboarding import OnboardingEtapaEntrada, OnboardingSaida
from app import onboarding_service

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("/me", response_model=OnboardingSaida)
def obter(
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
):
    return onboarding_service.obter_onboarding(usuario)


@router.post("/me/iniciar", response_model=OnboardingSaida)
def iniciar(
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
):
    return onboarding_service.iniciar_onboarding(usuario)


@router.post("/me/etapas/{chave_etapa}/concluir", response_model=OnboardingSaida)
def concluir_etapa(
    chave_etapa: str,
    dados: OnboardingEtapaEntrada,
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
):
    return onboarding_service.concluir_etapa(
        usuario,
        chave_etapa,
        dados.origem,
        dados.metadados,
    )


@router.post("/me/dispensar", response_model=OnboardingSaida)
def dispensar(
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
):
    return onboarding_service.dispensar_onboarding(usuario)


@router.post("/me/concluir", response_model=OnboardingSaida)
def concluir(
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
):
    return onboarding_service.concluir_onboarding(usuario)


@router.post("/me/restaurar", response_model=OnboardingSaida)
def restaurar(
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
):
    return onboarding_service.restaurar_onboarding(usuario)


@router.post("/me/revisar", response_model=OnboardingSaida)
def revisar(
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
):
    return onboarding_service.revisar_onboarding(usuario)
