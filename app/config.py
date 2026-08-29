"""Configuração central da API."""
import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

_ORIGENS_PADRAO = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://sgnc-web-frontend.vercel.app",
]

CORS_ORIGINS = [
    origem.strip()
    for origem in os.getenv("CORS_ORIGINS", ",".join(_ORIGENS_PADRAO)).split(",")
    if origem.strip()
]

# Previews só são aceitos dentro do namespace Vercel da própria conta/projeto.
CORS_ORIGIN_REGEX = os.getenv(
    "CORS_ORIGIN_REGEX",
    r"https://sgnc-web-frontend(?:-git-[a-z0-9-]+)?-lukaschamposki-7496s-projects\.vercel\.app",
)
