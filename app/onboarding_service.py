"""Persistência e regras do onboarding híbrido."""
import json
from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.auth import UsuarioLogado
from app.onboarding_manifesto import (
    VERSAO_ONBOARDING,
    chaves_por_origem,
    manifesto_papel,
    totais_papel,
)
from app.supabase_client import cliente_do_usuario


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _buscar_execucao(cliente, usuario: UsuarioLogado) -> dict | None:
    resposta = (
        cliente.table("onboarding_execucoes")
        .select("*")
        .eq("usuario_id", usuario.id)
        .eq("papel", usuario.papel)
        .eq("versao", VERSAO_ONBOARDING)
        .limit(1)
        .execute()
    )
    return resposta.data[0] if resposta.data else None


def _buscar_ou_criar_execucao(usuario: UsuarioLogado) -> tuple[object, dict]:
    cliente = cliente_do_usuario(usuario.token)
    execucao = _buscar_execucao(cliente, usuario)
    if execucao:
        return cliente, execucao

    try:
        resposta = (
            cliente.table("onboarding_execucoes")
            .insert(
                {
                    "usuario_id": usuario.id,
                    "papel": usuario.papel,
                    "versao": VERSAO_ONBOARDING,
                    "status": "nao_iniciado",
                }
            )
            .execute()
        )
        return cliente, resposta.data[0]
    except Exception:
        # Duas abas podem criar a mesma execução simultaneamente.
        execucao = _buscar_execucao(cliente, usuario)
        if execucao:
            return cliente, execucao
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Não foi possível iniciar o onboarding agora.",
        )


def _listar_etapas(cliente, execucao_id: str) -> list[dict]:
    resposta = (
        cliente.table("onboarding_etapas")
        .select("chave_etapa, origem, concluida_em")
        .eq("execucao_id", execucao_id)
        .order("criado_em")
        .execute()
    )
    return resposta.data or []


def _montar_resposta(
    cliente,
    execucao: dict,
    usuario: UsuarioLogado,
    *,
    modo_revisao: bool = False,
) -> dict:
    etapas = _listar_etapas(cliente, execucao["id"])
    concluidas = {item["chave_etapa"] for item in etapas}
    totais = totais_papel(usuario.papel)
    progresso = {}

    for origem in ("apresentacao", "checklist", "contextual"):
        chaves = chaves_por_origem(usuario.papel, origem)
        progresso[origem] = sum(chave in concluidas for chave in chaves)

    progresso["total"] = len(concluidas.intersection(manifesto_papel(usuario.papel)))
    apresentacao_pendente = any(
        chave not in concluidas
        for chave in chaves_por_origem(usuario.papel, "apresentacao")
    )

    return {
        "execucao_id": execucao["id"],
        "papel": usuario.papel,
        "versao": VERSAO_ONBOARDING,
        "status": execucao["status"],
        "iniciado_em": execucao.get("iniciado_em"),
        "concluido_em": execucao.get("concluido_em"),
        "dispensado_em": execucao.get("dispensado_em"),
        "etapas_concluidas": [
            {
                "chave": item["chave_etapa"],
                "origem": item["origem"],
                "concluida_em": item.get("concluida_em"),
            }
            for item in etapas
        ],
        "totais": totais,
        "progresso": progresso,
        "deve_exibir_apresentacao": (
            apresentacao_pendente and execucao["status"] != "dispensado"
        ),
        "modo_revisao": modo_revisao,
    }


def obter_onboarding(usuario: UsuarioLogado) -> dict:
    cliente, execucao = _buscar_ou_criar_execucao(usuario)
    return _montar_resposta(cliente, execucao, usuario)


def iniciar_onboarding(usuario: UsuarioLogado) -> dict:
    cliente, execucao = _buscar_ou_criar_execucao(usuario)
    if execucao["status"] == "nao_iniciado":
        agora = _agora()
        resposta = (
            cliente.table("onboarding_execucoes")
            .update(
                {
                    "status": "em_andamento",
                    "iniciado_em": agora,
                    "ultima_interacao_em": agora,
                    "atualizado_em": agora,
                }
            )
            .eq("id", execucao["id"])
            .execute()
        )
        execucao = resposta.data[0]
    return _montar_resposta(cliente, execucao, usuario)


def concluir_etapa(
    usuario: UsuarioLogado,
    chave_etapa: str,
    origem: str,
    metadados: dict,
) -> dict:
    manifesto = manifesto_papel(usuario.papel)
    origem_esperada = manifesto.get(chave_etapa)
    if origem_esperada is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Etapa de onboarding não encontrada para este papel.",
        )
    if origem != origem_esperada:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A origem informada não corresponde à etapa.",
        )
    if len(json.dumps(metadados, ensure_ascii=False)) > 4096:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Metadados da etapa excedem o limite permitido.",
        )

    cliente, execucao = _buscar_ou_criar_execucao(usuario)
    agora = _agora()
    cliente.table("onboarding_etapas").upsert(
        {
            "execucao_id": execucao["id"],
            "chave_etapa": chave_etapa,
            "origem": origem,
            "concluida_em": agora,
            "metadados": metadados,
        },
        on_conflict="execucao_id,chave_etapa",
    ).execute()

    etapas = _listar_etapas(cliente, execucao["id"])
    concluidas = {item["chave_etapa"] for item in etapas}
    obrigatorias = set(chaves_por_origem(usuario.papel, "apresentacao"))
    obrigatorias.update(chaves_por_origem(usuario.papel, "checklist"))
    finalizou = obrigatorias.issubset(concluidas)

    atualizacao = {
        "status": "concluido" if finalizou else "em_andamento",
        "ultima_interacao_em": agora,
        "atualizado_em": agora,
    }
    if execucao.get("iniciado_em") is None:
        atualizacao["iniciado_em"] = agora
    if finalizou:
        atualizacao["concluido_em"] = agora
        atualizacao["dispensado_em"] = None

    resposta = (
        cliente.table("onboarding_execucoes")
        .update(atualizacao)
        .eq("id", execucao["id"])
        .execute()
    )
    return _montar_resposta(cliente, resposta.data[0], usuario)


def dispensar_onboarding(usuario: UsuarioLogado) -> dict:
    cliente, execucao = _buscar_ou_criar_execucao(usuario)
    agora = _agora()
    resposta = (
        cliente.table("onboarding_execucoes")
        .update(
            {
                "status": "dispensado",
                "dispensado_em": agora,
                "ultima_interacao_em": agora,
                "atualizado_em": agora,
            }
        )
        .eq("id", execucao["id"])
        .execute()
    )
    return _montar_resposta(cliente, resposta.data[0], usuario)


def concluir_onboarding(usuario: UsuarioLogado) -> dict:
    cliente, execucao = _buscar_ou_criar_execucao(usuario)
    concluidas = {
        item["chave_etapa"]
        for item in _listar_etapas(cliente, execucao["id"])
    }
    apresentacao = set(chaves_por_origem(usuario.papel, "apresentacao"))
    if not apresentacao.issubset(concluidas):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conclua a apresentação inicial antes de finalizar.",
        )

    agora = _agora()
    resposta = (
        cliente.table("onboarding_execucoes")
        .update(
            {
                "status": "concluido",
                "concluido_em": agora,
                "dispensado_em": None,
                "ultima_interacao_em": agora,
                "atualizado_em": agora,
            }
        )
        .eq("id", execucao["id"])
        .execute()
    )
    return _montar_resposta(cliente, resposta.data[0], usuario)


def restaurar_onboarding(usuario: UsuarioLogado) -> dict:
    cliente, execucao = _buscar_ou_criar_execucao(usuario)
    agora = _agora()
    resposta = (
        cliente.table("onboarding_execucoes")
        .update(
            {
                "status": "em_andamento",
                "dispensado_em": None,
                "ultima_interacao_em": agora,
                "atualizado_em": agora,
            }
        )
        .eq("id", execucao["id"])
        .execute()
    )
    return _montar_resposta(cliente, resposta.data[0], usuario)


def revisar_onboarding(usuario: UsuarioLogado) -> dict:
    cliente, execucao = _buscar_ou_criar_execucao(usuario)
    return _montar_resposta(cliente, execucao, usuario, modo_revisao=True)
