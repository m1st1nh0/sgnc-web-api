from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class EvidenciaSaida(BaseModel):
    id: int
    nc_id: int
    nome_original: str
    url_temporaria: Optional[str] = None  # gerada na hora, expira
    enviado_por: Optional[str]
    criado_em: datetime
