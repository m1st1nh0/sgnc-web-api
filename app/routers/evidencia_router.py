from fastapi import APIRouter, Depends, File, UploadFile

from app.auth import UsuarioLogado, exigir_senha_definitiva
from app import evidencia_service

router = APIRouter(prefix="/nc/{nc_id}/evidencias", tags=["evidências"])


@router.get("")
def listar(
    nc_id: int,
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
):
    return evidencia_service.listar_evidencias(usuario, nc_id)


@router.post("")
def anexar(
    nc_id: int,
    arquivo: UploadFile = File(...),
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
):
    return evidencia_service.anexar_evidencia(usuario, nc_id, arquivo)


@router.delete("/{evidencia_id}")
def excluir(
    nc_id: int,
    evidencia_id: int,
    usuario: UsuarioLogado = Depends(exigir_senha_definitiva),
):
    evidencia_service.excluir_evidencia(usuario, nc_id, evidencia_id)
    return {"status": "excluida"}
