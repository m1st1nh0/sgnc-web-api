"""Validação defensiva de uploads de evidência."""
from __future__ import annotations

from io import BytesIO
from pathlib import PurePath
import re
import zipfile

from fastapi import HTTPException, UploadFile, status


TAMANHO_MAXIMO_BYTES = 15 * 1024 * 1024
TAMANHO_CHUNK = 1024 * 1024

MIME_CANONICO = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

MIME_ACEITO_POR_EXTENSAO = {
    extensao: {mime, "application/octet-stream"}
    for extensao, mime in MIME_CANONICO.items()
}
MIME_ACEITO_POR_EXTENSAO[".jpg"].add("image/jpg")
MIME_ACEITO_POR_EXTENSAO[".jpeg"].add("image/jpg")


def _nome_basename(nome: str | None) -> str:
    candidato = (nome or "").replace("\\", "/")
    nome_limpo = PurePath(candidato).name.strip()
    if not nome_limpo or nome_limpo in {".", ".."}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nome de arquivo inválido.",
        )
    return nome_limpo


def _extensao(nome: str) -> str:
    if "." not in nome:
        return ""
    return "." + nome.rsplit(".", 1)[-1].lower()


def _nome_storage(nome_original: str) -> str:
    seguro = re.sub(r"[^A-Za-z0-9._ -]+", "_", nome_original).strip(" .")
    return (seguro or "arquivo")[:120]


def _assinatura_simples_valida(extensao: str, conteudo: bytes) -> bool:
    if extensao == ".png":
        return conteudo.startswith(b"\x89PNG\r\n\x1a\n")
    if extensao in {".jpg", ".jpeg"}:
        return conteudo.startswith(b"\xff\xd8\xff")
    if extensao == ".gif":
        return conteudo.startswith((b"GIF87a", b"GIF89a"))
    if extensao == ".webp":
        return len(conteudo) >= 12 and conteudo[:4] == b"RIFF" and conteudo[8:12] == b"WEBP"
    if extensao == ".pdf":
        return conteudo.startswith(b"%PDF-")
    if extensao == ".doc":
        return conteudo.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    return False


def _office_zip_valido(extensao: str, conteudo: bytes) -> bool:
    if not conteudo.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(BytesIO(conteudo)) as pacote:
            nomes = set(pacote.namelist())
            if "[Content_Types].xml" not in nomes:
                return False
            if extensao == ".docx":
                return any(nome.startswith("word/") for nome in nomes)
            if extensao == ".xlsx":
                return any(nome.startswith("xl/") for nome in nomes)
    except (zipfile.BadZipFile, OSError):
        return False
    return False


def _validar_conteudo(extensao: str, conteudo: bytes) -> None:
    valido = (
        _office_zip_valido(extensao, conteudo)
        if extensao in {".docx", ".xlsx"}
        else _assinatura_simples_valida(extensao, conteudo)
    )
    if not valido:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O conteúdo do arquivo não corresponde ao tipo informado.",
        )


def ler_upload_validado(arquivo: UploadFile) -> tuple[bytes, str, str, str]:
    """Lê no máximo 15 MB e valida extensão, MIME e assinatura real."""
    nome_original = _nome_basename(arquivo.filename)
    extensao = _extensao(nome_original)
    if extensao not in MIME_CANONICO:
        aceitas = ", ".join(sorted(MIME_CANONICO))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tipo de arquivo não permitido. Aceitos: {aceitas}",
        )

    mime_declarado = (arquivo.content_type or "application/octet-stream").lower().split(";", 1)[0].strip()
    if mime_declarado not in MIME_ACEITO_POR_EXTENSAO[extensao]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O Content-Type enviado não corresponde à extensão do arquivo.",
        )

    partes: list[bytes] = []
    total = 0
    while True:
        pedaco = arquivo.file.read(TAMANHO_CHUNK)
        if not pedaco:
            break
        total += len(pedaco)
        if total > TAMANHO_MAXIMO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Arquivo maior que 15 MB.",
            )
        partes.append(pedaco)

    conteudo = b"".join(partes)
    if not conteudo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Arquivo vazio não é permitido.",
        )

    _validar_conteudo(extensao, conteudo)
    return conteudo, nome_original, _nome_storage(nome_original), MIME_CANONICO[extensao]
