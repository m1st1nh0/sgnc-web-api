"""Timeline e durações do ciclo de uma Não Conformidade.

Os timestamps de ciclo são persistidos na própria NC. O histórico serve como
trilha de auditoria dos eventos; as durações abaixo usam os timestamps de
domínio para que o PR04 possa agregar métricas sem reinterpretar textos.
"""
from datetime import datetime, timezone

from app.auth import UsuarioLogado
from app.supabase_client import cliente_servico


def _parse_datetime(valor) -> datetime | None:
    if valor is None:
        return None
    if isinstance(valor, datetime):
        dt = valor
    else:
        try:
            dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _segundos(inicio: datetime | None, fim: datetime | None) -> int | None:
    if inicio is None or fim is None or fim < inicio:
        return None
    return int((fim - inicio).total_seconds())


def calcular_duracoes(nc: dict, agora: datetime | None = None) -> dict:
    """Calcula durações canônicas, em segundos, sem alterar o registro."""
    criado = _parse_datetime(nc.get("criado_em"))
    validado = _parse_datetime(nc.get("validado_em"))
    feedback = _parse_datetime(nc.get("feedback_aplicado_em"))
    aceito = _parse_datetime(nc.get("aceito_em"))
    decidido = _parse_datetime(nc.get("decidido_em"))

    status = nc.get("status")
    inicio_etapa = None
    if status == "aberta":
        inicio_etapa = criado
    elif status in {"validada", "aguardando_analise", "aguardando_feedback"}:
        inicio_etapa = validado or _parse_datetime(nc.get("enviado_em")) or criado
    elif status == "aguardando_aceite":
        inicio_etapa = feedback or validado or criado

    agora_utc = agora or datetime.now(timezone.utc)
    if agora_utc.tzinfo is None:
        agora_utc = agora_utc.replace(tzinfo=timezone.utc)
    agora_utc = agora_utc.astimezone(timezone.utc)

    return {
        "criacao_ate_validacao_segundos": _segundos(criado, validado),
        "validacao_ate_feedback_segundos": _segundos(validado, feedback),
        "feedback_ate_aceite_segundos": _segundos(feedback, aceito),
        "ciclo_total_segundos": _segundos(criado, aceito),
        "criacao_ate_decisao_segundos": _segundos(criado, decidido),
        "etapa_atual": status if inicio_etapa is not None else None,
        "etapa_atual_desde": inicio_etapa.isoformat() if inicio_etapa else None,
        "tempo_etapa_atual_segundos": _segundos(inicio_etapa, agora_utc),
    }


def obter_timeline(
    usuario: UsuarioLogado,
    nc: dict,
) -> dict:
    """Retorna eventos da NC já autorizada pela camada de leitura/RLS."""
    servico = cliente_servico()
    eventos = (
        servico.table("historico_nc")
        .select(
            "id, usuario_id, status_anterior, status_novo, observacao, criado_em"
        )
        .eq("nc_id", nc["id"])
        .order("criado_em")
        .execute()
        .data
    )

    # O contrato legado esconde campos sensíveis do autor quando ele não é
    # ADM, colaborador alvo ou responsável. A timeline não pode reintroduzir
    # o motivo de invalidação/feedback por meio de `observacao`.
    eh_autor = nc.get("aberto_por") == usuario.id
    pode_ver_completo = (
        usuario.papel == "adm"
        or nc.get("colaborador_id") == usuario.id
        or nc.get("responsavel_id") == usuario.id
        or usuario.papel == "supervisor"
    )
    if eh_autor and not pode_ver_completo:
        eventos = [{**evento, "observacao": None} for evento in eventos]

    return {
        "nc_id": nc["id"],
        "status_atual": nc.get("status"),
        "duracoes": calcular_duracoes(nc),
        "eventos": eventos,
    }
