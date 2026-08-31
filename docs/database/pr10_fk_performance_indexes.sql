-- Índices de suporte às foreign keys apontadas pelo Performance Advisor.
-- IF NOT EXISTS mantém a operação idempotente em ambientes já corrigidos.

create index if not exists idx_causas_criado_por
  on public.causas (criado_por);

create index if not exists idx_evidencias_enviado_por
  on public.evidencias (enviado_por);

create index if not exists idx_historico_nc_usuario
  on public.historico_nc (usuario_id);

create index if not exists idx_medidas_disciplinares_aplicada_por
  on public.medidas_disciplinares (aplicada_por);

create index if not exists idx_medidas_disciplinares_causa
  on public.medidas_disciplinares (causa_id);

create index if not exists idx_medidas_disciplinares_colaborador
  on public.medidas_disciplinares (colaborador_id);
