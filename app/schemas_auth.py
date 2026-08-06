"""
"Schemas" aqui são só a forma dos dados que entram e saem da API.
FastAPI usa isso (via Pydantic) para validar automaticamente o que
o React manda, e para gerar a documentação interativa (/docs).
"""
from pydantic import BaseModel, EmailStr


class LoginEntrada(BaseModel):
    email: EmailStr
    senha: str


class LoginSaida(BaseModel):
    token: str
    usuario_id: str
    nome: str
    email: str
    papel: str
    senha_provisoria: bool
