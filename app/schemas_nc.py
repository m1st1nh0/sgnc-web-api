"""Formato dos dados de entrada/saída dos endpoints de NC."""
from datetime import date, datetime
from typing import Optional
from typing import Literal
from pydantic import BaseModel


class NcEntrada(BaseModel):
    """Dados enviados ao abrir ou editar uma NC.
    Qualquer papel pode abrir (aberto_por = usuário logado, definido
    pela API, não pelo cliente).
    Note: não há mais campo de "setor" aqui - ele é preenchido
    automaticamente pela API a partir do cadastro do colaborador
    (usuarios.setor), não é mais texto livre por NC."""
    data: Optional[date] = None
    chamado: Optional[str] = None
    colaborador_id: Optional[str] = None  # uuid de quem a NC é sobre
    criticidade: str = "Baixa"
    descricao: Optional[str] = None
    causas: list[str] = []


class NcAvaliar(BaseModel):
    """ADM avalia se a NC é procedente."""
    decisao: str  # "validar" ou "invalidar"
    motivo_invalidacao: Optional[str] = None  # obrigatório se decisao == "invalidar"


class NcFeedback(BaseModel):
    """ADM aplica o parecer/combinado com o colaborador."""
    feedback: str


TEXTO_ACEITE_ESPERADO = "li e concordo com a não conformidade e com o feedback aplicado"


class NcAceite(BaseModel):
    """Aceite formal do colaborador. O texto precisa bater
    (ignorando maiúsculas/espaços nas pontas) com a frase padrão,
    funcionando como uma confirmação com fricção proposital."""
    texto_aceite: str


class NcSaida(BaseModel):
    id: int
    data: Optional[date]
    chamado: Optional[str]
    setor: Optional[str]
    colaborador: Optional[str]
    colaborador_id: Optional[str]
    aberto_por: str
    responsavel_id: Optional[str]
    criticidade: str
    reincidencia: str
    status: str
    descricao: Optional[str]
    setor_responsavel: Optional[str]
    causas: list[str] = []
    motivo_invalidacao: Optional[str]
    feedback: Optional[str]
    texto_aceite: Optional[str]
    validado_em: Optional[datetime]
    feedback_aplicado_em: Optional[datetime]
    aceito_em: Optional[datetime]
    criado_em: datetime
    atualizado_em: datetime



class MedidaDisciplinarEntrada(BaseModel):
    """
    Dados enviados pelo responsável da qualidade
    ao registrar manualmente uma medida disciplinar.
    """

    causa_id: int
    nc_id: int
    ocorrencia_gatilho: int

    tipo: Literal[
        "advertencia",
        "suspensao",
        "avaliar_justa_causa",
    ]

    dias_suspensao: Optional[int] = None
    observacao: Optional[str] = None