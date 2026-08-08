from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://sgnc-web-frontend.vercel.app",
    ],
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
