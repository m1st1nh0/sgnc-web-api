-- PR01 - etapa A (backward-compatible)
-- Aplicada em produção antes do deploy da API nova.
-- Mantida no repositório como histórico/documentação do rollout.

alter type public.status_nc
    add value if not exists 'aguardando_feedback' after 'validada';
