from io import BytesIO
from pathlib import Path
import zipfile

from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.security_hardening import LoginRateLimiter, validar_senha_forte
from app.upload_security import ler_upload_validado


# Política de senha vale somente para criação/troca, não para login existente.
validar_senha_forte("SenhaForte1!")
for senha_fraca in [
    "curta1!A",
    "semm maiuscula1!".replace(" ", ""),
    "SEMMINUSCULA1!",
    "SemNumero!!",
    "SemSimbolo123A",
]:
    try:
        validar_senha_forte(senha_fraca)
        raise AssertionError(f"senha fraca aceita: {senha_fraca}")
    except HTTPException as exc:
        assert exc.status_code == 400


# Rate-limit determinístico, com chave irreversível e reset após sucesso.
agora = [1000.0]
limiter = LoginRateLimiter(
    max_falhas=3,
    janela_segundos=60,
    bloqueio_segundos=120,
    clock=lambda: agora[0],
)
chave = limiter.chave("Usuario@Example.com", "127.0.0.1")
assert "usuario@example.com" not in chave.lower()
assert len(chave) == 64
limiter.verificar(chave)
limiter.registrar_falha(chave)
limiter.registrar_falha(chave)
limiter.verificar(chave)
limiter.registrar_falha(chave)
try:
    limiter.verificar(chave)
    raise AssertionError("rate limit deveria bloquear")
except HTTPException as exc:
    assert exc.status_code == 429
    assert int(exc.headers["Retry-After"]) > 0

agora[0] += 121
limiter.verificar(chave)
limiter.registrar_falha(chave)
limiter.registrar_sucesso(chave)
limiter.verificar(chave)


def upload(nome: str, mime: str, conteudo: bytes) -> UploadFile:
    return UploadFile(
        file=BytesIO(conteudo),
        filename=nome,
        headers=Headers({"content-type": mime}),
    )


# Assinatura real de imagem e sanitização de nome.
png = b"\x89PNG\r\n\x1a\n" + b"dados"
conteudo, original, storage, mime = ler_upload_validado(
    upload("../../evidência ?.png", "image/png", png)
)
assert conteudo == png
assert original == "evidência ?.png"
assert "/" not in storage and "\\" not in storage
assert mime == "image/png"

# Extensão não basta: conteúdo falso é rejeitado.
try:
    ler_upload_validado(upload("arquivo.pdf", "application/pdf", b"isto nao e pdf"))
    raise AssertionError("PDF falso deveria ser rejeitado")
except HTTPException as exc:
    assert exc.status_code == 400

# MIME incompatível também é rejeitado.
try:
    ler_upload_validado(upload("foto.png", "application/pdf", png))
    raise AssertionError("MIME incompatível deveria ser rejeitado")
except HTTPException as exc:
    assert exc.status_code == 400

# DOCX precisa ser um ZIP OOXML que contenha estrutura word/.
buf = BytesIO()
with zipfile.ZipFile(buf, "w") as pacote:
    pacote.writestr("[Content_Types].xml", "<Types/>")
    pacote.writestr("word/document.xml", "<document/>")
conteudo_docx, _, _, mime_docx = ler_upload_validado(
    upload(
        "documento.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        buf.getvalue(),
    )
)
assert conteudo_docx.startswith(b"PK\x03\x04")
assert mime_docx.endswith("wordprocessingml.document")

# Limite é aplicado durante a leitura, não depois de aceitar conteúdo arbitrário.
try:
    ler_upload_validado(
        upload(
            "grande.pdf",
            "application/pdf",
            b"%PDF-" + b"x" * (15 * 1024 * 1024),
        )
    )
    raise AssertionError("arquivo acima do limite deveria falhar")
except HTTPException as exc:
    assert exc.status_code == 400


security = Path("app/security_hardening.py").read_text(encoding="utf-8")
auth_router = Path("app/routers/auth_router.py").read_text(encoding="utf-8")
usuario_service = Path("app/usuario_service.py").read_text(encoding="utf-8")
upload_security = Path("app/upload_security.py").read_text(encoding="utf-8")
evidencia = Path("app/evidencia_service.py").read_text(encoding="utf-8")
main = Path("app/main.py").read_text(encoding="utf-8")
headers = Path("app/security_headers.py").read_text(encoding="utf-8")

assert "sha256" in security
assert "Retry-After" in security
assert "LOGIN_RATE_LIMITER.verificar" in auth_router
assert "LOGIN_RATE_LIMITER.registrar_falha" in auth_router
assert usuario_service.count("validar_senha_forte") >= 3  # import + create + change
assert "TAMANHO_CHUNK" in upload_security
assert "zipfile.ZipFile" in upload_security
assert "ler_upload_validado" in evidencia
assert "arquivo.file.read()" not in evidencia
assert "remove([caminho_storage])" in evidencia
assert "allow_methods=[\"GET\", \"POST\", \"PUT\", \"PATCH\", \"DELETE\", \"OPTIONS\"]" in main
assert "SecurityHeadersMiddleware" in main
assert "X-Content-Type-Options" in headers
assert "Cache-Control" in headers

print("PR08 SECURITY HARDENING TESTS PASSED")
