"""Manifesto versionado das jornadas de onboarding do SGNC."""

VERSAO_ONBOARDING = 1
PAPEIS_ONBOARDING = ("adm", "supervisor", "funcionario")

_ETAPAS_COMPARTILHADAS = {
    "apresentacao_boas_vindas": "apresentacao",
    "apresentacao_fluxo_nc": "apresentacao",
    "apresentacao_documentos": "apresentacao",
    "checklist_conhecer_painel": "checklist",
    "checklist_abrir_nc": "checklist",
    "checklist_visualizar_nc": "checklist",
    "checklist_dossie": "checklist",
    "checklist_baixar_pdf": "checklist",
    "dica_abertura_colaborador": "contextual",
    "dica_abertura_evidencias": "contextual",
    "dica_nc_pdf": "contextual",
}

_ETAPAS_ESPECIFICAS = {
    "adm": {
        "apresentacao_papel_adm": "apresentacao",
        "checklist_avaliar_nc": "checklist",
        "checklist_feedback": "checklist",
        "checklist_insights": "checklist",
        "checklist_usuarios": "checklist",
        "dica_nc_avaliacao": "contextual",
        "dica_nc_feedback": "contextual",
        "dica_gestao_usuarios": "contextual",
    },
    "supervisor": {
        "apresentacao_papel_supervisor": "apresentacao",
        "checklist_equipe": "checklist",
        "checklist_acompanhar_nc": "checklist",
        "checklist_insights": "checklist",
        "dica_equipe_direta": "contextual",
        "dica_dossie_equipe": "contextual",
    },
    "funcionario": {
        "apresentacao_papel_funcionario": "apresentacao",
        "checklist_evidencias": "checklist",
        "checklist_aceite": "checklist",
        "dica_nc_aceite": "contextual",
        "dica_dossie_pessoal": "contextual",
    },
}


def manifesto_papel(papel: str) -> dict[str, str]:
    """Retorna as chaves válidas e suas origens para o papel informado."""
    if papel not in PAPEIS_ONBOARDING:
        raise ValueError("Papel sem jornada de onboarding.")
    return {**_ETAPAS_COMPARTILHADAS, **_ETAPAS_ESPECIFICAS[papel]}


def chaves_por_origem(papel: str, origem: str) -> list[str]:
    return [
        chave
        for chave, origem_etapa in manifesto_papel(papel).items()
        if origem_etapa == origem
    ]


def totais_papel(papel: str) -> dict[str, int]:
    manifesto = manifesto_papel(papel)
    totais = {
        origem: sum(1 for valor in manifesto.values() if valor == origem)
        for origem in ("apresentacao", "checklist", "contextual")
    }
    totais["total"] = len(manifesto)
    return totais
