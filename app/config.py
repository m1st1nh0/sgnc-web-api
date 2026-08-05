"""
Configuração central da API: lê as variáveis do arquivo .env.

Por que um arquivo .env? Para nunca deixar senhas/chaves escritas
direto no código-fonte (que pode ir parar num repositório Git).
"""
import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
