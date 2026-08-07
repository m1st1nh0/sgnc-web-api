from datetime import date, datetime, timezone, timedelta

from fastapi import HTTPException, status

from app.auth import UsuarioLogado
from app.schemas_nc import TEXTO_ACEITE_ESPERADO
from app.supabase_client import cliente_do_usuario, cliente_servico


STATUS_QUE_CONTAM_REINCIDENCIA = [
    "validada",
    "aguardando_analise",
    "aguardando_aceite",
    "concluida",
]


# =====================================================
# Causas (autocomplete que cresce com o uso)
# =====================================================


def obter_ou_criar_causas(
    cliente, nomes_causas: list[str], usuario_id: str
) -> list[int]:
    ids = []
    for nome_bruto in nomes_causas:
        nome = nome_bruto.strip()
        if not nome:
            continue

        existente = (
            cliente.table("causas")
            .select("id")
            .ilike("descricao", nome)
            .execute()
        )
        if existente.data:
            ids.append(existente.data[0]["id"])
            continue

        criada = (
            cliente.table("causas")
            .insert({"descricao": nome, "criado_por": usuario_id})
            .execute()
        )
        ids.append(criada.data[0]["id"])
    return ids


def listar_causas_conhecidas(cliente) -> list[str]:
    resultado = (
        cliente.table("causas")
        .select("descricao")
        .order("descricao")
        .execute()
    )
    return [linha["descricao"] for linha in resultado.data]


# =====================================================
# Reincidência (regra antiga, mantida por compatibilidade)
# =====================================================


def calcular_reincidencia(
    cliente, causa_ids: list[int], nc_id_excluir: int | None = None
) -> str:
    if not causa_ids:
        return "Não"

    resultado = (
        cliente.table("nc_causas")
        .select("nc_id")
        .in_("causa_id", causa_ids)
        .execute()
    )
    nc_ids_relacionadas = {linha["nc_id"] for linha in resultado.data}
    if nc_id_excluir is not None:
        nc_ids_relacionadas.discard(nc_id_excluir)

    return "Sim" if nc_ids_relacionadas else "Não"


# =====================================================
# Reincidência por causa - janela de 12 meses
# =====================================================


def calcular_ocorrencia_causa_12m(
    cliente,
    colaborador_id: str,
    causa_id: int,
    nc_id_atual: int | None = None,
    data_referencia: date | None = None,
) -> int:
    """
    Calcula o número da próxima ocorrência de uma causa para um colaborador.

    A contagem considera:
    - o mesmo colaborador;
    - a mesma causa;
    - NCs dos últimos 12 meses;
    - somente NCs validadas ou concluídas;
    - a NC atual fora da contagem histórica.

    Exemplo:

        Histórico:
        ocorrência 1
        ocorrência 2
        ocorrência 3

        Resultado para a nova NC:
        ocorrência 4
    """

    referencia = data_referencia or date.today()
    data_inicial = referencia - timedelta(days=365)

    resultado_ncs = (
        cliente.table("nao_conformidades")
        .select("id")
        .eq("colaborador_id", colaborador_id)
        .in_("status", STATUS_QUE_CONTAM_REINCIDENCIA)
        .gte("data", data_inicial.isoformat())
        .lte("data", referencia.isoformat())
        .execute()
    )

    ids_ncs = {
        nc["id"]
        for nc in resultado_ncs.data
        if nc.get("id") is not None and nc["id"] != nc_id_atual
    }

    if not ids_ncs:
        return 1

    resultado_causas = (
        cliente.table("nc_causas")
        .select("nc_id")
        .eq("causa_id", causa_id)
        .in_("nc_id", list(ids_ncs))
        .execute()
    )

    ocorrencias_existentes = {
        relacao["nc_id"]
        for relacao in resultado_causas.data
        if relacao.get("nc_id") is not None
    }

    return len(ocorrencias_existentes) + 1


def calcular_ocorrencias_da_nc(
    cliente,
    nc_id: int,
    colaborador_id: str,
    data_referencia: date | None = None,
) -> list[dict]:
    """
    Calcula a ocorrência individual de cada causa relacionada à NC.

    Exemplo de retorno:

    [
        {
            "causa_id": 2,
            "ocorrencia_numero": 4,
        },
        {
            "causa_id": 5,
            "ocorrencia_numero": 1,
        },
    ]
    """

    resultado_causas = (
        cliente.table("nc_causas")
        .select("causa_id")
        .eq("nc_id", nc_id)
        .execute()
    )

    ocorrencias = []

    for relacao in resultado_causas.data:
        causa_id = relacao["causa_id"]

        numero = calcular_ocorrencia_causa_12m(
            cliente=cliente,
            colaborador_id=colaborador_id,
            causa_id=causa_id,
            nc_id_atual=nc_id,
            data_referencia=data_referencia,
        )

        ocorrencias.append(
            {
                "causa_id": causa_id,
                "ocorrencia_numero": numero,
            }
        )

    return ocorrencias


def registrar_ocorrencias_da_nc(
    cliente,
    nc_id: int,
    colaborador_id: str,
    data_referencia: date | None = None,
) -> list[dict]:
    """
    Calcula e grava o número da ocorrência de cada causa da NC.
    """

    ocorrencias = calcular_ocorrencias_da_nc(
        cliente=cliente,
        nc_id=nc_id,
        colaborador_id=colaborador_id,
        data_referencia=data_referencia,
    )

    for ocorrencia in ocorrencias:
        (
            cliente.table("nc_causas")
            .update(
                {
                    "ocorrencia_numero": ocorrencia["ocorrencia_numero"],
                }
            )
            .eq("nc_id", nc_id)
            .eq("causa_id", ocorrencia["causa_id"])
            .execute()
        )

    return ocorrencias


# =====================================================
# Medida disciplinar sugerida
# =====================================================


def decidir_medida_disciplina(
    ocorrencia_numero: int,
) -> str | None:
    """
    Decide se uma ocorrência deve gerar uma medida disciplinar sugerida.

    Regra definida:

        1ª, 2ª e 3ª ocorrência -> nenhuma medida
        4ª ocorrência -> advertência
        7ª ocorrência -> advertência
        10ª ocorrência -> advertência
        13ª ocorrência -> suspensão
        16ª ocorrência -> suspensão
        19ª ocorrência -> suspensão
        22ª ocorrência -> avaliar justa causa/permanência

    Retorna:
        None quando nenhuma medida deve ser sugerida;
        string com a medida quando houver gatilho.
    """

    if ocorrencia_numero <= 3:
        return None

    posicao_no_ciclo = ocorrencia_numero - 3

    if (posicao_no_ciclo - 1) % 3 != 0:
        return None

    numero_da_medida = ((posicao_no_ciclo - 1) // 3) + 1

    if numero_da_medida <= 3:
        return "advertencia"

    if numero_da_medida <= 6:
        return "suspensao"

    return "avaliar_justa_causa"


# =====================================================
# Medidas disciplinares manuais
# =====================================================


def registrar_medida_disciplinar(
    usuario: UsuarioLogado,
    dados,
) -> dict:
    """
    Registra manualmente uma medida disciplinar.

    A função não é chamada durante a validação da NC.
    A aplicação depende de uma ação explícita do responsável autorizado.
    """

    if usuario.papel != "adm":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Apenas um responsável autorizado pode "
                "registrar medidas disciplinares."
            ),
        )

    cliente = cliente_do_usuario(usuario.token)

    # =================================================
    # Busca e valida a NC
    # =================================================

    resultado_nc = (
        cliente.table("nao_conformidades")
        .select("id, colaborador_id")
        .eq("id", dados.nc_id)
        .execute()
    )

    if not resultado_nc.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Não conformidade não encontrada.",
        )

    nc = resultado_nc.data[0]

    if not nc.get("colaborador_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A não conformidade não possui um colaborador associado."
            ),
        )

    # =================================================
    # Valida se a causa pertence à NC
    # =================================================

    resultado_causa = (
        cliente.table("nc_causas")
        .select("causa_id, ocorrencia_numero")
        .eq("nc_id", dados.nc_id)
        .eq("causa_id", dados.causa_id)
        .execute()
    )

    if not resultado_causa.data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A causa informada não pertence à não conformidade.",
        )

    relacao_causa = resultado_causa.data[0]
    ocorrencia_atual = relacao_causa.get("ocorrencia_numero")

    if ocorrencia_atual is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A causa ainda não possui ocorrência contabilizada.",
        )

    if ocorrencia_atual != dados.ocorrencia_gatilho:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A ocorrência informada não corresponde "
                "à ocorrência registrada para esta causa."
            ),
        )

    # =================================================
    # Valida os dados da medida
    # =================================================

    if dados.ocorrencia_gatilho < 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Não é possível registrar medida disciplinar "
                "antes da quarta ocorrência."
            ),
        )

    if dados.tipo == "suspensao":
        if dados.dias_suspensao is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Informe a quantidade de dias "
                    "para registrar uma suspensão."
                ),
            )

        if not 1 <= dados.dias_suspensao <= 30:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A suspensão deve possuir entre 1 e 30 dias.",
            )
    elif dados.dias_suspensao is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A quantidade de dias só deve ser informada "
                "para medidas do tipo suspensão."
            ),
        )

    # =================================================
    # Impede duplicidade
    # =================================================

    medida_existente = (
        cliente.table("medidas_disciplinares")
        .select("id")
        .eq("nc_id", dados.nc_id)
        .eq("causa_id", dados.causa_id)
        .eq("ocorrencia_gatilho", dados.ocorrencia_gatilho)
        .execute()
    )

    if medida_existente.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Já existe uma medida registrada "
                "para esta NC, causa e ocorrência."
            ),
        )

    # =================================================
    # Registra a medida manualmente
    # =================================================

    payload = {
        "colaborador_id": nc["colaborador_id"],
        "causa_id": dados.causa_id,
        "nc_id": dados.nc_id,
        "ocorrencia_gatilho": dados.ocorrencia_gatilho,
        "tipo": dados.tipo,
        "status": "aplicada",
        "dias_suspensao": dados.dias_suspensao,
        "aplicada_por": usuario.id,
        "observacao": (
            dados.observacao.strip() if dados.observacao else None
        ),
    }

    resultado = (
        cliente.table("medidas_disciplinares")
        .insert(payload)
        .execute()
    )

    if not resultado.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Não foi possível registrar a medida disciplinar.",
        )

    return resultado.data[0]


# =====================================================
# Montagem do objeto de saída (junta NC + causas)
# =====================================================


def _montar_saida_nc(cliente, nc: dict) -> dict:
    causas_rel = (
        cliente.table("nc_causas")
        .select("causas(descricao)")
        .eq("nc_id", nc["id"])
        .execute()
    )
    nc["causas"] = [
        linha["causas"]["descricao"]
        for linha in causas_rel.data
        if linha.get("causas")
    ]
    return nc


# =====================================================
# Abertura e edição
# =====================================================


def _buscar_colaborador(cliente, colaborador_id: str) -> dict | None:
    """Busca nome e setor do colaborador para preencher a NC
    automaticamente - essas informações não são mais digitadas
    livremente no formulário, vêm do cadastro do usuário."""
    if not colaborador_id:
        return None
    resultado = (
        cliente.table("usuarios")
        .select("nome, setor")
        .eq("id", colaborador_id)
        .execute()
    )
    return resultado.data[0] if resultado.data else None


def criar_nc(usuario: UsuarioLogado, dados) -> dict:
    """Qualquer papel pode abrir uma NC. Nasce em 'aberta', aguardando
    avaliação do ADM sobre se é procedente."""
    cliente = cliente_do_usuario(usuario.token)

    causa_ids = obter_ou_criar_causas(cliente, dados.causas, usuario.id)
    reincidencia = calcular_reincidencia(cliente, causa_ids)

    colaborador = _buscar_colaborador(cliente, dados.colaborador_id)

    payload = {
        "chamado": dados.chamado,
        "setor": colaborador["setor"] if colaborador else None,
        "colaborador": colaborador["nome"] if colaborador else None,
        "colaborador_id": dados.colaborador_id,
        "criticidade": dados.criticidade,
        "reincidencia": reincidencia,
        "status": "aberta",
        "descricao": dados.descricao,
        "aberto_por": usuario.id,
        "setor_responsavel": colaborador["setor"] if colaborador else None,
    }
    if dados.data is not None:
        payload["data"] = dados.data.isoformat()

    criada = cliente.table("nao_conformidades").insert(payload).execute()
    nc = criada.data[0]

    if causa_ids:
        vinculos = [
            {"nc_id": nc["id"], "causa_id": cid} for cid in causa_ids
        ]
        cliente.table("nc_causas").insert(vinculos).execute()

    _registrar_historico(nc["id"], usuario.id, None, "aberta", "NC aberta")

    return _montar_saida_nc(cliente, nc)


def listar_ncs(usuario: UsuarioLogado) -> list[dict]:
    """RLS decide o que aparece: autor sempre vê a própria; colaborador
    e supervisor veem a partir de 'validada' em diante; ADM vê tudo."""
    cliente = cliente_do_usuario(usuario.token)
    resultado = (
        cliente.table("nao_conformidades")
        .select("*")
        .order("criado_em", desc=True)
        .execute()
    )
    return [_montar_saida_nc(cliente, nc) for nc in resultado.data]


def buscar_nc(usuario: UsuarioLogado, nc_id: int) -> dict:
    """Busca uma NC e aplica filtro de campos sensíveis dependendo de
    quem está acessando (autor, colaborador, responsável, ADM)."""
    cliente = cliente_do_usuario(usuario.token)
    resultado = (
        cliente.table("nao_conformidades")
        .select("*")
        .eq("id", nc_id)
        .execute()
    )
    if not resultado.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="NC não encontrada.",
        )

    nc = _montar_saida_nc(cliente, resultado.data[0])

    eh_aberto_por = nc.get("aberto_por") == usuario.id
    eh_colaborador = nc.get("colaborador_id") == usuario.id
    eh_responsavel = nc.get("responsavel_id") == usuario.id
    eh_adm = usuario.papel == "adm"

    pode_ver_completo = eh_adm or eh_colaborador or eh_responsavel

    if eh_aberto_por and not pode_ver_completo:
        nc["motivo_invalidacao"] = None
        nc["feedback"] = None
        nc["texto_aceite"] = None
        nc["validado_em"] = None
        nc["feedback_aplicado_em"] = None
        nc["aceito_em"] = None

    return nc


def atualizar_nc(usuario: UsuarioLogado, nc_id: int, dados) -> dict:
    """Edição livre de campos só faz sentido enquanto a NC ainda não
    foi avaliada (status 'aberta'). Depois disso, ADM usa os endpoints
    específicos (avaliar/feedback) em vez de editar tudo de novo."""
    cliente = cliente_do_usuario(usuario.token)

    causa_ids = obter_ou_criar_causas(cliente, dados.causas, usuario.id)
    reincidencia = calcular_reincidencia(
        cliente, causa_ids, nc_id_excluir=nc_id
    )

    colaborador = _buscar_colaborador(cliente, dados.colaborador_id)

    payload = {
        "chamado": dados.chamado,
        "setor": colaborador["setor"] if colaborador else None,
        "colaborador": colaborador["nome"] if colaborador else None,
        "colaborador_id": dados.colaborador_id,
        "criticidade": dados.criticidade,
        "reincidencia": reincidencia,
        "descricao": dados.descricao,
        "setor_responsavel": colaborador["setor"] if colaborador else None,
    }
    if dados.data is not None:
        payload["data"] = dados.data.isoformat()

    atualizada = (
        cliente.table("nao_conformidades")
        .update(payload)
        .eq("id", nc_id)
        .execute()
    )
    if not atualizada.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="NC não encontrada.",
        )

    cliente.table("nc_causas").delete().eq("nc_id", nc_id).execute()
    if causa_ids:
        vinculos = [
            {"nc_id": nc_id, "causa_id": cid} for cid in causa_ids
        ]
        cliente.table("nc_causas").insert(vinculos).execute()

    return _montar_saida_nc(cliente, atualizada.data[0])


def excluir_nc(usuario: UsuarioLogado, nc_id: int) -> None:
    cliente = cliente_do_usuario(usuario.token)
    cliente.table("nao_conformidades").delete().eq("id", nc_id).execute()


# =====================================================
# Fluxo de avaliação / envio / feedback / aceite
# =====================================================


def avaliar_nc(usuario: UsuarioLogado, nc_id: int, dados) -> dict:
    """
    ADM decide se a NC é procedente.

    Fluxo:
        aberta -> validada
        aberta -> invalidada

    A reincidência somente é contabilizada quando a NC é validada.
    NCs invalidadas não entram na contagem.
    """
    cliente = cliente_do_usuario(usuario.token)

    atual = buscar_nc(usuario, nc_id)

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

    if dados.decisao == "invalidar" and not (
        dados.motivo_invalidacao and dados.motivo_invalidacao.strip()
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Informe o motivo da invalidação.",
        )

    # =================================================
    # Fluxo de invalidação
    # =================================================

    if dados.decisao == "invalidar":
        payload = {
            "responsavel_id": usuario.id,
            "status": "invalidada",
            "motivo_invalidacao": dados.motivo_invalidacao.strip(),
        }

        atualizada = (
            cliente.table("nao_conformidades")
            .update(payload)
            .eq("id", nc_id)
            .execute()
        )

        if not atualizada.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Não foi possível atualizar a NC.",
            )

        _registrar_historico(
            nc_id=nc_id,
            usuario_id=usuario.id,
            status_anterior="aberta",
            status_novo="invalidada",
            observacao=dados.motivo_invalidacao.strip(),
        )

        return _montar_saida_nc(cliente, atualizada.data[0])

    # =================================================
    # Fluxo de validação
    # =================================================

    colaborador_id = atual.get("colaborador_id")

    if not colaborador_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Não é possível contabilizar a reincidência "
                "sem um colaborador definido."
            ),
        )

    data_referencia = date.today()

    if atual.get("data"):
        try:
            data_referencia = date.fromisoformat(str(atual["data"]))
        except ValueError:
            data_referencia = date.today()

    ocorrencias = calcular_ocorrencias_da_nc(
        cliente=cliente,
        nc_id=nc_id,
        colaborador_id=colaborador_id,
        data_referencia=data_referencia,
    )

    for ocorrencia in ocorrencias:
        ocorrencia["medida_sugerida"] = decidir_medida_disciplina(
            ocorrencia["ocorrencia_numero"]
        )

    payload = {
        "responsavel_id": usuario.id,
        "status": "validada",
        "validado_em": datetime.now(timezone.utc).isoformat(),
    }

    atualizada = (
        cliente.table("nao_conformidades")
        .update(payload)
        .eq("id", nc_id)
        .execute()
    )

    if not atualizada.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Não foi possível validar a NC.",
        )

    for ocorrencia in ocorrencias:
        (
            cliente.table("nc_causas")
            .update(
                {
                    "ocorrencia_numero": ocorrencia["ocorrencia_numero"],
                }
            )
            .eq("nc_id", nc_id)
            .eq("causa_id", ocorrencia["causa_id"])
            .execute()
        )

    _registrar_historico(
        nc_id=nc_id,
        usuario_id=usuario.id,
        status_anterior="aberta",
        status_novo="validada",
        observacao="NC validada e ocorrência contabilizada",
    )

    resposta = _montar_saida_nc(cliente, atualizada.data[0])
    resposta["ocorrencias"] = ocorrencias

    return resposta


def enviar_nc(usuario: UsuarioLogado, nc_id: int) -> dict:
    """validada -> aguardando_analise. A partir daqui colaborador e
    supervisor passam a enxergar a NC (regra garantida pelo RLS)."""
    cliente = cliente_do_usuario(usuario.token)

    atual = buscar_nc(usuario, nc_id)
    if atual["status"] != "validada":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Só é possível enviar NCs que estão validadas.",
        )
    if not atual.get("colaborador_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Defina o colaborador responsável antes de enviar a NC.",
        )

    atualizada = (
        cliente.table("nao_conformidades")
        .update({"status": "aguardando_analise"})
        .eq("id", nc_id)
        .execute()
    )

    _registrar_historico(
        nc_id,
        usuario.id,
        "validada",
        "aguardando_analise",
        "NC enviada ao colaborador e supervisor",
    )

    return _montar_saida_nc(cliente, atualizada.data[0])


def aplicar_feedback(usuario: UsuarioLogado, nc_id: int, dados) -> dict:
    """aguardando_analise -> aguardando_aceite. O parecer/combinado
    fica visível ao colaborador e ao supervisor a partir daqui."""
    cliente = cliente_do_usuario(usuario.token)

    atual = buscar_nc(usuario, nc_id)
    if atual["status"] != "aguardando_analise":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Só é possível aplicar feedback em NCs que estão aguardando análise.",
        )

    if not dados.feedback or not dados.feedback.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Informe o feedback.",
        )

    atualizada = (
        cliente.table("nao_conformidades")
        .update(
            {
                "status": "aguardando_aceite",
                "feedback": dados.feedback,
                "feedback_aplicado_em": datetime.now(timezone.utc).isoformat(),
            }
        )
        .eq("id", nc_id)
        .execute()
    )

    _registrar_historico(
        nc_id,
        usuario.id,
        "aguardando_analise",
        "aguardando_aceite",
        "Feedback aplicado",
    )

    return _montar_saida_nc(cliente, atualizada.data[0])


def aceitar_nc(usuario: UsuarioLogado, nc_id: int, dados) -> dict:
    """Aceite formal do colaborador. Exige digitar a frase de
    confirmação exata (ignorando maiúsculas e espaços nas pontas) -
    funciona como uma 'assinatura' de baixa fricção mas intencional."""
    cliente = cliente_do_usuario(usuario.token)

    texto_normalizado = " ".join(
        dados.texto_aceite.strip().lower().split()
    )
    if texto_normalizado != TEXTO_ACEITE_ESPERADO:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Para confirmar, digite exatamente: "{TEXTO_ACEITE_ESPERADO}"',
        )

    payload = {
        "status": "concluida",
        "texto_aceite": dados.texto_aceite,
        "aceito_em": datetime.now(timezone.utc).isoformat(),
    }

    atualizada = (
        cliente.table("nao_conformidades")
        .update(payload)
        .eq("id", nc_id)
        .execute()
    )
    if not atualizada.data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Não foi possível registrar o aceite "
                "(a NC pode não ser sua ou não estar aguardando aceite)."
            ),
        )

    _registrar_historico(
        nc_id,
        usuario.id,
        "aguardando_aceite",
        "concluida",
        "Aceite formal do colaborador",
    )

    return _montar_saida_nc(cliente, atualizada.data[0])


def _registrar_historico(
    nc_id: int,
    usuario_id: str,
    status_anterior: str | None,
    status_novo: str,
    observacao: str | None,
):
    cliente_servico().table("historico_nc").insert(
        {
            "nc_id": nc_id,
            "usuario_id": usuario_id,
            "status_anterior": status_anterior,
            "status_novo": status_novo,
            "observacao": observacao,
        }
    ).execute()

# =====================================================
# Estatísticas de reincidência por colaborador
# =====================================================


def obter_estatisticas_colaborador(
    usuario_logado: UsuarioLogado,
    colaborador_id: str,
) -> dict:
    """
    Monta um resumo de reincidência e medidas disciplinares de um
    colaborador, respeitando a hierarquia:

    - o próprio colaborador pode ver os seus dados;
    - o supervisor direto pode ver os dados do supervisionado;
    - o ADM pode ver os dados de qualquer colaborador.
    """
    cliente = cliente_do_usuario(usuario_logado.token)

    resultado_usuario = (
        cliente.table("usuarios")
        .select("id, nome, setor, papel, supervisor_id")
        .eq("id", colaborador_id)
        .execute()
    )

    if not resultado_usuario.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não encontrado.",
        )

    colaborador = resultado_usuario.data[0]

    eh_o_proprio = usuario_logado.id == colaborador_id
    eh_supervisor_direto = (
        colaborador.get("supervisor_id") == usuario_logado.id
    )
    eh_adm = usuario_logado.papel == "adm"

    if not (eh_o_proprio or eh_supervisor_direto or eh_adm):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Você não tem permissão para ver essas estatísticas.",
        )

    referencia = date.today()
    data_inicial = referencia - timedelta(days=365)

    resultado_ncs = (
        cliente.table("nao_conformidades")
        .select("id")
        .eq("colaborador_id", colaborador_id)
        .in_("status", STATUS_QUE_CONTAM_REINCIDENCIA)
        .gte("data", data_inicial.isoformat())
        .lte("data", referencia.isoformat())
        .execute()
    )

    ids_ncs_12m = [nc["id"] for nc in resultado_ncs.data]

    linhas_causas = []
    if ids_ncs_12m:
        resultado_causas = (
            cliente.table("nc_causas")
            .select("nc_id, causa_id, ocorrencia_numero, causas(descricao)")
            .in_("nc_id", ids_ncs_12m)
            .execute()
        )
        linhas_causas = resultado_causas.data

    agrupado: dict[int, dict] = {}

    for linha in linhas_causas:
        causa_id = linha["causa_id"]
        causa_info = linha.get("causas") or {}
        descricao = causa_info.get("descricao")

        if causa_id not in agrupado:
            agrupado[causa_id] = {
                "causa_id": causa_id,
                "causa": descricao,
                "ocorrencias_12m": 0,
                "ultima_ocorrencia_numero": None,
            }

        agrupado[causa_id]["ocorrencias_12m"] += 1

        numero = linha.get("ocorrencia_numero")
        atual = agrupado[causa_id]["ultima_ocorrencia_numero"]
        if numero is not None and (atual is None or numero > atual):
            agrupado[causa_id]["ultima_ocorrencia_numero"] = numero

    resultado_medidas = (
        cliente.table("medidas_disciplinares")
        .select(
            "id, causa_id, nc_id, ocorrencia_gatilho, tipo, "
            "status, dias_suspensao, data_aplicacao, observacao"
        )
        .eq("colaborador_id", colaborador_id)
        .order("data_aplicacao", desc=True)
        .execute()
    )

    medidas_por_causa: dict[int, list[dict]] = {}
    for medida in resultado_medidas.data:
        medidas_por_causa.setdefault(medida["causa_id"], []).append(medida)

    causas_saida = []
    for causa_id, info in agrupado.items():
        info["medidas"] = medidas_por_causa.get(causa_id, [])
        causas_saida.append(info)

    causas_saida.sort(
        key=lambda c: c["ocorrencias_12m"],
        reverse=True,
    )

    return {
        "usuario_id": colaborador["id"],
        "nome": colaborador["nome"],
        "setor": colaborador.get("setor"),
        "total_nc_12m": len(ids_ncs_12m),
        "causas": causas_saida,
    }