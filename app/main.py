from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import CORS_ORIGINS, CORS_ORIGIN_REGEX
from app.security_headers import SecurityHeadersMiddleware
from app.routers import (
    auth_router,
    nc_router,
    usuario_router,
    evidencia_router,
    insights_router,
)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(auth_router.router)
app.include_router(nc_router.router)
app.include_router(usuario_router.router)
app.include_router(evidencia_router.router)
app.include_router(insights_router.router)


@app.get("/")
def raiz():
    return {
        "status": "ok",
        "servico": "SGNC API",
        "dominio": "recorrencia-v2",
        "seguranca": "hardening-v1",
    }
