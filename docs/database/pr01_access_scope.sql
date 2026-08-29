-- PR01 - Escopo de equipe, fluxo aguardando_feedback e fechamento da Data API
-- Aplicar SOMENTE após o backend desta PR estar implantado.
-- O backend novo usa service_role para mutações e mantém o token do usuário
-- apenas para leituras protegidas por RLS.

begin;

-- =============================================================
-- 1. Novo status do fluxo simplificado
-- =============================================================

alter type public.status_nc
    add value if not exists 'aguardando_feedback' after 'validada';

-- =============================================================
-- 2. Helpers privados usados exclusivamente pelo RLS
-- =============================================================

create schema if not exists private;
revoke all on schema private from public, anon;
grant usage on schema private to authenticated;

create or replace function private.meu_papel()
returns public.papel_usuario
language sql
stable
security definer
set search_path = ''
as $$
    select u.papel
      from public.usuarios as u
     where u.id = auth.uid();
$$;

create or replace function private.e_meu_subordinado(alvo_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1
          from public.usuarios as u
         where u.id = alvo_id
           and u.supervisor_id = auth.uid()
    );
$$;

revoke all on function private.meu_papel() from public, anon;
revoke all on function private.e_meu_subordinado(uuid) from public, anon;
grant execute on function private.meu_papel() to authenticated;
grant execute on function private.e_meu_subordinado(uuid) to authenticated;

-- Trigger sem dados sensíveis, mas com search_path imutável.
create or replace function public.set_atualizado_em()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.atualizado_em = pg_catalog.now();
    return new;
end;
$$;

-- =============================================================
-- 3. RLS de usuários: ADM global; supervisor equipe direta;
--    funcionário somente o próprio cadastro.
-- =============================================================

drop policy if exists usuarios_adm_le_todos on public.usuarios;
drop policy if exists usuarios_ler_proprio on public.usuarios;
drop policy if exists usuarios_qualquer_autenticado_le on public.usuarios;
drop policy if exists usuarios_supervisor_le_subordinados on public.usuarios;

create policy usuarios_select_escopo
on public.usuarios
for select
to authenticated
using (
    id = (select auth.uid())
    or private.meu_papel() = 'adm'::public.papel_usuario
    or supervisor_id = (select auth.uid())
);

-- =============================================================
-- 4. RLS de NCs
--    aberta/invalidada/validada-legado: ADM + autor
--    após validação: ADM + autor + alvo + supervisor direto
-- =============================================================

drop policy if exists nc_adm_le_tudo on public.nao_conformidades;
drop policy if exists nc_autor_le_propria on public.nao_conformidades;
drop policy if exists nc_colaborador_le_proprias on public.nao_conformidades;
drop policy if exists nc_supervisor_le_equipe on public.nao_conformidades;

create policy nc_select_escopo
on public.nao_conformidades
for select
to authenticated
using (
    private.meu_papel() = 'adm'::public.papel_usuario
    or aberto_por = (select auth.uid())
    or (
        status in (
            'aguardando_feedback'::public.status_nc,
            'aguardando_analise'::public.status_nc,
            'aguardando_aceite'::public.status_nc,
            'concluida'::public.status_nc
        )
        and (
            colaborador_id = (select auth.uid())
            or private.e_meu_subordinado(colaborador_id)
        )
    )
);

-- =============================================================
-- 5. Tabelas derivadas seguem a visibilidade da NC
-- =============================================================

drop policy if exists nc_causas_visivel_conforme_nc on public.nc_causas;
create policy nc_causas_select_conforme_nc
on public.nc_causas
for select
to authenticated
using (
    exists (
        select 1
          from public.nao_conformidades as nc
         where nc.id = nc_causas.nc_id
    )
);

drop policy if exists evidencias_visivel_conforme_nc on public.evidencias;
create policy evidencias_select_conforme_nc
on public.evidencias
for select
to authenticated
using (
    exists (
        select 1
          from public.nao_conformidades as nc
         where nc.id = evidencias.nc_id
    )
);

drop policy if exists historico_visivel_conforme_nc on public.historico_nc;
create policy historico_select_conforme_nc
on public.historico_nc
for select
to authenticated
using (
    exists (
        select 1
          from public.nao_conformidades as nc
         where nc.id = historico_nc.nc_id
    )
);

drop policy if exists causas_qualquer_autenticado_le on public.causas;
create policy causas_select_autenticado
on public.causas
for select
to authenticated
using (true);

-- Medidas: próprio colaborador, supervisor direto ou ADM.
drop policy if exists medidas_select_adm on public.medidas_disciplinares;
drop policy if exists medidas_select_colaborador on public.medidas_disciplinares;
drop policy if exists medidas_select_supervisor on public.medidas_disciplinares;
create policy medidas_select_escopo
on public.medidas_disciplinares
for select
to authenticated
using (
    private.meu_papel() = 'adm'::public.papel_usuario
    or colaborador_id = (select auth.uid())
    or private.e_meu_subordinado(colaborador_id)
);

-- =============================================================
-- 6. Mutações passam exclusivamente pelo FastAPI/service_role
-- =============================================================

-- Remove policies de escrita do token de usuário.
drop policy if exists usuarios_adm_insere on public.usuarios;
drop policy if exists usuarios_adm_atualiza on public.usuarios;

drop policy if exists nc_qualquer_autenticado_insere on public.nao_conformidades;
drop policy if exists nc_adm_atualiza on public.nao_conformidades;
drop policy if exists nc_autor_edita_propria on public.nao_conformidades;
drop policy if exists nc_colaborador_aceita on public.nao_conformidades;
drop policy if exists nc_adm_exclui on public.nao_conformidades;

drop policy if exists causas_qualquer_autenticado_insere on public.causas;
drop policy if exists nc_causas_qualquer_autenticado_insere on public.nc_causas;
drop policy if exists nc_causas_adm_exclui on public.nc_causas;
drop policy if exists evidencias_adm_insere on public.evidencias;
drop policy if exists evidencias_adm_exclui on public.evidencias;
drop policy if exists medidas_insert_adm on public.medidas_disciplinares;

-- Anon não precisa acessar tabelas da aplicação.
revoke all privileges on table
    public.usuarios,
    public.nao_conformidades,
    public.causas,
    public.nc_causas,
    public.evidencias,
    public.historico_nc,
    public.medidas_disciplinares
from anon;

-- Usuários autenticados ficam somente com SELECT.
revoke insert, update, delete, truncate, references, trigger on table
    public.usuarios,
    public.nao_conformidades,
    public.causas,
    public.nc_causas,
    public.evidencias,
    public.historico_nc,
    public.medidas_disciplinares
from authenticated;

grant select on table
    public.usuarios,
    public.nao_conformidades,
    public.causas,
    public.nc_causas,
    public.evidencias,
    public.historico_nc,
    public.medidas_disciplinares
    to authenticated;

-- =============================================================
-- 7. Converte estados legados depois que a API nova já estiver online
-- =============================================================

update public.nao_conformidades
   set status = 'aguardando_feedback'::public.status_nc,
       enviado_em = coalesce(enviado_em, validado_em, pg_catalog.now())
 where status in (
    'validada'::public.status_nc,
    'aguardando_analise'::public.status_nc
 );

-- As policies já não dependem mais dos helpers públicos antigos.
drop function if exists public.e_meu_subordinado(uuid);
drop function if exists public.meu_papel();

commit;
