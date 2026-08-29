"""PR03: workflow atômico + timeline.

As leituras e regras de autorização continuam herdadas do PR01/PR02. As
mudanças de estado passam por RPCs internas que gravam status, timestamps e
histórico na mesma transação do Postgres.
"""
from fastapi import HTTPException, status

from app.auth import UsuarioLogado
from app.schemas_nc import TEXTO_ACEITE_ESPERADO
from app import nc_service as legacy
from app import nc_service_pr02 as pr02
from app import nc_service_v2 as base
from app.timeline_service import calcular_duracoes, obter_timeline as montar_timeline


listar_ncs = pr02.listar_ncs
listar_causas_conhecidas = pr02.listar_causas_conhecidas
atualizar_nc = pr02.atualizar_nc
excluir_nc = pr02.excluir_nc
registrar_medida_disciplinar = pr02.registrar_medida_disciplinar
decidir_medida_disciplina = pr02.decidir_medida_disciplina


def _normalizar(dados) -> dict:
    return pr02._normalizar_retorno_rpc(dados)


def _chamar_rpc(nome: str, parametros: dict) -> dict:
    chamada = base._servico().rpc(nome, parametros).execute()
    return _normalizar(chamada.data)


def _erro_padrao(resultado: dict) -> None:
    erro = resultado.get("erro")
    if erro == "nc_nao_encontrada":
        raise HTTPException(status_code=404, detail="NC não encontrada.")
    if erro in {"nc_nao_aberta", "status_invalido"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A NC já foi alterada por outro processo.",
        )
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Não foi possível concluir a transição da NC.",
    )


def buscar_nc(usuario: UsuarioLogado, nc_id: int) -> dict:
    nc = pr02.buscar_nc(usuario, nc_id)
    nc["duracoes"] = calcular_duracoes(nc)
    return nc


def obter_timeline(usuario: UsuarioLogado, nc_id: int) -> dict:
    nc = buscar_nc(usuario, nc_id)  # leitura autenticada/RLS autoriza primeiro
    return montar_timeline(usuario, nc)


def criar_nc(usuario: UsuarioLogado, dados) -> dict:
    """NC + vínculos de causa + evento inicial são gravados atomicamente."""
    servico = base._servico()
    causa_ids = legacy.obter_ou_criar_causas(servico, dados.causas, usuario.id)
    colaborador = legacy._buscar_colaborador(servico, dados.colaborador_id)

    if dados.colaborador_id and not colaborador:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Colaborador informado não existe.",
        )

    resultado = _chamar_rpc(
        "criar_nc_com_historico_v3",
        {
            "p_data": dados.data.isoformat() if dados.data is not None else None,
            "p_chamado": dados.chamado,
            "p_setor": colaborador["setor"] if colaborador else None,
            "p_colaborador": colaborador["nome"] if colaborador else None,
            "p_colaborador_id": dados.colaborador_id,
            "p_criticidade": dados.criticidade,
            "p_descricao": dados.descricao,
            "p_aberto_por": usuario.id,
            "p_setor_responsavel": colaborador["setor"] if colaborador else None,
            "p_causa_ids": causa_ids,
        },
    )
    if not resultado.get("ok"):
        _erro_padrao(resultado)

    criada = base._buscar_nc_servico(resultado["nc_id"])
    return legacy._montar_saida_nc(servico, criada)


def avaliar_nc(usuario: UsuarioLogado, nc_id: int, dados) -> dict:
    base._exigir_adm(usuario)
    atual = base._buscar_nc_servico(nc_id)

    if atual["status"] != "aberta":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Só é possível avaliar NCs que estão em 'aberta'.",
        )
    if dados.decisao not in ("validar", "invalidar"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="decisao deve ser 'validar' ou 'invalidar'.",
        )

    if dados.decisao == "invalidar":
        motivo = (dados.motivo_invalidacao or "").strip()
        if not motivo:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Informe o motivo da invalidação.",
            )
        resultado = _chamar_rpc(
            "invalidar_nc_v3",
            {
                "p_nc_id": nc_id,
                "p_responsavel_id": usuario.id,
                "p_motivo": motivo,
            },
        )
        if not resultado.get("ok"):
            if resultado.get("erro") == "motivo_ausente":
                raise HTTPException(status_code=400, detail="Informe o motivo da invalidação.")
            _erro_padrao(resultado)
        return buscar_nc(usuario, nc_id)

    if not atual.get("colaborador_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Defina o colaborador analisado antes de validar a NC.",
        )

    resultado = _chamar_rpc(
        "validar_nc_com_workflow_v3",
        {"p_nc_id": nc_id, "p_responsavel_id": usuario.id},
    )
    if not resultado.get("ok"):
        if resultado.get("erro") == "colaborador_ausente":
            raise HTTPException(
                status_code=400,
                detail="Defina o colaborador analisado antes de validar a NC.",
            )
        _erro_padrao(resultado)

    ocorrencias = resultado.get("ocorrencias") or []
    for ocorrencia in ocorrencias:
        ocorrencia["medida_sugerida"] = legacy.decidir_medida_disciplina(
            ocorrencia["ocorrencia_numero"]
        )

    resposta = buscar_nc(usuario, nc_id)
    resposta["ocorrencias"] = ocorrencias
    return resposta


def enviar_nc(usuario: UsuarioLogado, nc_id: int) -> dict:
    """Compatibilidade atômica para o status legado `validada`."""
    base._exigir_adm(usuario)
    resultado = _chamar_rpc(
        "enviar_nc_legada_v3",
        {"p_nc_id": nc_id, "p_responsavel_id": usuario.id},
    )
    if not resultado.get("ok"):
        _erro_padrao(resultado)
    return buscar_nc(usuario, nc_id)


def aplicar_feedback(usuario: UsuarioLogado, nc_id: int, dados) -> dict:
    base._exigir_adm(usuario)
    feedback = (dados.feedback or "").strip()
    if not feedback:
        raise HTTPException(status_code=400, detail="Informe o feedback.")

    resultado = _chamar_rpc(
        "aplicar_feedback_nc_v3",
        {
            "p_nc_id": nc_id,
            "p_responsavel_id": usuario.id,
            "p_feedback": feedback,
        },
    )
    if not resultado.get("ok"):
        if resultado.get("erro") == "feedback_ausente":
            raise HTTPException(status_code=400, detail="Informe o feedback.")
        _erro_padrao(resultado)
    return buscar_nc(usuario, nc_id)


def aceitar_nc(usuario: UsuarioLogado, nc_id: int, dados) -> dict:
    texto_normalizado = " ".join(dados.texto_aceite.strip().lower().split())
    if texto_normalizado != TEXTO_ACEITE_ESPERADO:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Para confirmar, digite exatamente: "{TEXTO_ACEITE_ESPERADO}"',
        )

    atual = pr02.buscar_nc(usuario, nc_id)
    if atual.get("colaborador_id") != usuario.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Somente o colaborador analisado pode registrar o aceite.",
        )

    resultado = _chamar_rpc(
        "aceitar_nc_v3",
        {
            "p_nc_id": nc_id,
            "p_colaborador_id": usuario.id,
            "p_texto_aceite": dados.texto_aceite,
        },
    )
    if not resultado.get("ok"):
        if resultado.get("erro") == "colaborador_incorreto":
            raise HTTPException(
                status_code=403,
                detail="Somente o colaborador analisado pode registrar o aceite.",
            )
        _erro_padrao(resultado)
    return buscar_nc(usuario, nc_id)
