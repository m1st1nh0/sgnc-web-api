"""Compatibilidade temporária entre o fluxo legado e o status V2.

PR02 substituirá a regra antiga de reincidência. Até lá, os helpers legados
precisam reconhecer ``aguardando_feedback`` para não ignorar NCs já validadas
no novo fluxo.
"""
from app import nc_service as legacy

legacy.STATUS_QUE_CONTAM_REINCIDENCIA = [
    "validada",
    "aguardando_analise",
    "aguardando_feedback",
    "aguardando_aceite",
    "concluida",
]
