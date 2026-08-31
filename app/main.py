from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import (
    auth_router,
    nc_router,
    usuario_router,
    evidencia_router,
    insights_router,
    relatorios_router,
    onboarding_router,
)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://sgnc-web-frontend.vercel.app",
        "https://sgnc-web-frontend-git-refa-bfa2cb-lukaschamposki-7496s-projects.vercel.app",
    ],
    allow_origin_regex=(
        r"https://sgnc-web-frontend-git-[a-z0-9-]+-"
        r"lukaschamposki-7496s-projects\.vercel\.app"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(nc_router.router)
app.include_router(usuario_router.router)
app.include_router(evidencia_router.router)
app.include_router(insights_router.router)
app.include_router(relatorios_router.router)
app.include_router(onboarding_router.router)


@app.get("/")
def raiz():
    return {
        "status": "ok",
        "servico": "SGNC API",
        "dominio": "recorrencia-v2",
    }
