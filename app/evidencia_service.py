"""Evidências privadas armazenadas no Supabase Storage."""
import uuid as uuidlib

from fastapi import HTTPException, UploadFile, status

from app.auth import UsuarioLogado
from app.supabase_client import cliente_servico
from app import nc_service
from app.upload_security import ler_upload_validado

NOME_BUCKET = "evidencias"


def anexar_evidencia(usuario: UsuarioLogado, nc_id: int, arquivo: UploadFile) -> dict:
    """Anexa evidência somente a uma NC aberta e acessível ao usuário."""
    nc = nc_service.buscar_nc(usuario, nc_id)

    if nc["status"] != "aberta":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Só é possível anexar evidências enquanto a NC está em 'aberta'.",
        )

    conteudo, nome_original, nome_storage, mime_canonico = ler_upload_validado(arquivo)
    caminho_storage = f"nc_{nc_id}/{uuidlib.uuid4().hex}_{nome_storage}"

    servico = cliente_servico()
    servico.storage.from_(NOME_BUCKET).upload(
        caminho_storage,
        conteudo,
        file_options={"content-type": mime_canonico},
    )

    try:
        registrada = (
            servico.table("evidencias")
            .insert(
                {
                    "nc_id": nc_id,
                    "caminho_storage": caminho_storage,
                    "nome_original": nome_original,
                    "enviado_por": usuario.id,
                }
            )
            .execute()
        )
    except Exception:
        # Evita arquivo órfão se o registro no banco falhar após o Storage aceitar.
        # A limpeza é best-effort para não mascarar a falha original do banco.
        try:
            servico.storage.from_(NOME_BUCKET).remove([caminho_storage])
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Não foi possível registrar a evidência.",
        )

    return registrada.data[0]


def listar_evidencias(usuario: UsuarioLogado, nc_id: int) -> list[dict]:
    """Confirma acesso à NC e devolve URLs assinadas por 10 minutos."""
    nc_service.buscar_nc(usuario, nc_id)

    servico = cliente_servico()
    resultado = (
        servico.table("evidencias")
        .select("*")
        .eq("nc_id", nc_id)
        .order("criado_em")
        .execute()
    )

    evidencias = resultado.data
    for evidencia in evidencias:
        assinatura = servico.storage.from_(NOME_BUCKET).create_signed_url(
            evidencia["caminho_storage"], 600
        )
        evidencia["url_temporaria"] = assinatura.get("signedURL") or assinatura.get("signedUrl")

    return evidencias


def excluir_evidencia(usuario: UsuarioLogado, nc_id: int, evidencia_id: int) -> None:
    nc = nc_service.buscar_nc(usuario, nc_id)

    if usuario.papel != "adm" and nc["status"] != "aberta":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Só é possível remover evidências enquanto a NC está em 'aberta' (ou sendo o ADM).",
        )

    servico = cliente_servico()
    existente = (
        servico.table("evidencias")
        .select("*")
        .eq("id", evidencia_id)
        .eq("nc_id", nc_id)
        .execute()
    )
    if not existente.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evidência não encontrada.",
        )

    caminho_storage = existente.data[0]["caminho_storage"]
    servico.storage.from_(NOME_BUCKET).remove([caminho_storage])
    servico.table("evidencias").delete().eq("id", evidencia_id).execute()
