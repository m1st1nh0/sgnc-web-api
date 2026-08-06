from fastapi import APIRouter, Depends, File, UploadFile

from app.auth import UsuarioLogado, usuario_atual
from app import evidencia_service

router = APIRouter(prefix="/nc/{nc_id}/evidencias", tags=["evidências"])


@router.get("")
def listar(nc_id: int, usuario: UsuarioLogado = Depends(usuario_atual)):
    """Lista as evidências de uma NC, cada uma com uma URL temporária
    (expira em 10 minutos) para visualizar/baixar o arquivo."""
    return evidencia_service.listar_evidencias(usuario, nc_id)


@router.post("")
def anexar(nc_id: int, arquivo: UploadFile = File(...), usuario: UsuarioLogado = Depends(usuario_atual)):
    """Anexa um arquivo à NC. Qualquer usuário com acesso à NC pode
    anexar, mas só enquanto ela está em 'aberta'."""
    return evidencia_service.anexar_evidencia(usuario, nc_id, arquivo)


@router.delete("/{evidencia_id}")
def excluir(nc_id: int, evidencia_id: int, usuario: UsuarioLogado = Depends(usuario_atual)):
    evidencia_service.excluir_evidencia(usuario, nc_id, evidencia_id)
    return {"status": "excluida"}
