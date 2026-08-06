"""
Evidências (arquivos anexados a uma NC), armazenadas no Supabase
Storage (bucket privado 'evidencias').

Decisão de design: todo upload/download passa pela API usando o
cliente de SERVIÇO (que ignora RLS do Storage). A permissão de
"quem pode ver/anexar o quê" é resolvida checando acesso à NC via
nc_service.buscar_nc() (que já respeita RLS da tabela
nao_conformidades) - assim a regra de negócio mora num lugar só.

Organização dos arquivos no bucket: "nc_<id>/<uuid>_<nome_original>"
"""
import uuid as uuidlib

from fastapi import HTTPException, UploadFile, status

from app.auth import UsuarioLogado
from app.supabase_client import cliente_servico
from app import nc_service

NOME_BUCKET = "evidencias"

# Tipos de arquivo aceitos (mesma ideia do gerar_pdf.py original,
# que já sabia identificar imagens; aqui restringimos o upload)
EXTENSOES_PERMITIDAS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".doc", ".docx", ".xlsx"}

TAMANHO_MAXIMO_BYTES = 15 * 1024 * 1024  # 15 MB por arquivo


def _validar_extensao(nome_arquivo: str) -> None:
    sufixo = "." + nome_arquivo.rsplit(".", 1)[-1].lower() if "." in nome_arquivo else ""
    if sufixo not in EXTENSOES_PERMITIDAS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tipo de arquivo não permitido. Aceitos: {', '.join(sorted(EXTENSOES_PERMITIDAS))}",
        )


def anexar_evidencia(usuario: UsuarioLogado, nc_id: int, arquivo: UploadFile) -> dict:
    """Qualquer usuário com acesso à NC pode anexar, mas só enquanto
    ela ainda está 'aberta' (antes de enviada ao fluxo de avaliação)."""
    nc = nc_service.buscar_nc(usuario, nc_id)  # 404 se não tiver acesso

    if nc["status"] != "aberta":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Só é possível anexar evidências enquanto a NC está em 'aberta'.",
        )

    _validar_extensao(arquivo.filename)

    conteudo = arquivo.file.read()
    if len(conteudo) > TAMANHO_MAXIMO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Arquivo maior que 15 MB.",
        )

    caminho_storage = f"nc_{nc_id}/{uuidlib.uuid4().hex}_{arquivo.filename}"

    servico = cliente_servico()
    servico.storage.from_(NOME_BUCKET).upload(
        caminho_storage,
        conteudo,
        file_options={"content-type": arquivo.content_type or "application/octet-stream"},
    )

    registrada = (
        servico.table("evidencias")
        .insert({
            "nc_id": nc_id,
            "caminho_storage": caminho_storage,
            "nome_original": arquivo.filename,
            "enviado_por": usuario.id,
        })
        .execute()
    )

    return registrada.data[0]


def listar_evidencias(usuario: UsuarioLogado, nc_id: int) -> list[dict]:
    """Confirma acesso à NC (via RLS embutido em buscar_nc) e devolve
    a lista de evidências, cada uma com uma URL temporária assinada
    (expira, então não pode ser compartilhada permanentemente)."""
    nc_service.buscar_nc(usuario, nc_id)  # 404 se não tiver acesso

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
            evidencia["caminho_storage"], 600  # expira em 10 minutos
        )
        evidencia["url_temporaria"] = assinatura.get("signedURL") or assinatura.get("signedUrl")

    return evidencias


def excluir_evidencia(usuario: UsuarioLogado, nc_id: int, evidencia_id: int) -> None:
    nc = nc_service.buscar_nc(usuario, nc_id)  # 404 se não tiver acesso

    if usuario.papel != "adm" and nc["status"] != "aberta":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Só é possível remover evidências enquanto a NC está em 'aberta' (ou sendo o ADM).",
        )

    servico = cliente_servico()
    existente = (
        servico.table("evidencias").select("*").eq("id", evidencia_id).eq("nc_id", nc_id).execute()
    )
    if not existente.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidência não encontrada.")

    caminho_storage = existente.data[0]["caminho_storage"]
    servico.storage.from_(NOME_BUCKET).remove([caminho_storage])
    servico.table("evidencias").delete().eq("id", evidencia_id).execute()
