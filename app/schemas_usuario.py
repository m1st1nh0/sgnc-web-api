from typing import Optional
from pydantic import BaseModel, EmailStr


class UsuarioEntrada(BaseModel):
    nome: str
    email: EmailStr
    papel: str  # "adm" | "supervisor" | "funcionario"
    setor: Optional[str] = None
    supervisor_id: Optional[str] = None
    senha_inicial: str


class UsuarioEdicao(BaseModel):
    """Campos editáveis após o cadastro. Email não entra aqui —
    trocar email no Supabase Auth é uma operação separada e mais
    delicada (envolve reconfirmação), então deixamos fora do CRUD
    básico por ora."""
    nome: str
    papel: str
    setor: Optional[str] = None
    supervisor_id: Optional[str] = None


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
