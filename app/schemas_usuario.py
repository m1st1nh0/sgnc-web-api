from typing import Optional

from pydantic import BaseModel, EmailStr


class UsuarioEntrada(BaseModel):
    nome: str
    email: EmailStr
    papel: str  # "adm" | "supervisor" | "funcionario"
    setor: Optional[str] = None
    supervisor_id: Optional[str] = None  # uuid; obrigatório se papel != "adm"
    senha_inicial: str


class UsuarioSaida(BaseModel):
    id: str
    nome: str
    email: str
    papel: str
    setor: Optional[str]
    supervisor_id: Optional[str]
    ativo: bool
    senha_provisoria: bool


class TrocarSenhaEntrada(BaseModel):
    senha_atual: str
    senha_nova: str
