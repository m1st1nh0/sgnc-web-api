from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import auth_router, nc_router, usuario_router, evidencia_router

app = FastAPI(
    title="SGNC API",
    description="API do Sistema de Gestão de Não Conformidades",
    version="0.1.0",
)

# CORS: permite que o React (rodando em outro endereço/porta)
# consiga chamar esta API a partir do navegador.
# Em produção, troque "*" pela URL real do frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://sgnc-web-frontend-SEU-ID.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(nc_router.router)
app.include_router(usuario_router.router)
app.include_router(evidencia_router.router)


@app.get("/")
def raiz():
    return {"status": "ok", "servico": "SGNC API"}
