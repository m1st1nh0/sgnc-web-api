-- PR01 - etapa A (backward-compatible)
-- Pode ser aplicada antes do deploy da API nova.
-- O valor novo precisa estar committed antes de ser usado por policies/updates.

alter type public.status_nc
    add value if not exists 'aguardando_feedback' after 'validada';
