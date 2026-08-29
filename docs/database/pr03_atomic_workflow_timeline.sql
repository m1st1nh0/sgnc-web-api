-- PR03 - Workflow atômico, timeline e durações confiáveis
--
-- Objetivos:
-- 1. status + timestamps + histórico na mesma transação;
-- 2. preservar a reincidência V2 como fonte única, sem duplicar sua regra;
-- 3. manter as RPCs internas fechadas ao navegador (service_role only);
-- 4. completar timestamps legados a partir do histórico quando possível.

begin;

create or replace function public.criar_nc_com_historico_v3(
    p_data date,
    p_chamado text,
    p_setor text,
    p_colaborador text,
    p_colaborador_id uuid,
    p_criticidade text,
    p_descricao text,
    p_aberto_por uuid,
    p_setor_responsavel text,
    p_causa_ids bigint[]
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_nc_id bigint;
    v_agora timestamptz := pg_catalog.now();
begin
    insert into public.nao_conformidades (
        data, chamado, setor, colaborador, colaborador_id, criticidade,
        reincidencia, status, descricao, aberto_por, setor_responsavel,
        criado_em, atualizado_em
    ) values (
        coalesce(p_data, current_date), p_chamado, p_setor, p_colaborador,
        p_colaborador_id, p_criticidade, 'Não', 'aberta'::public.status_nc,
        p_descricao, p_aberto_por, p_setor_responsavel, v_agora, v_agora
    ) returning id into v_nc_id;

    if p_causa_ids is not null and pg_catalog.cardinality(p_causa_ids) > 0 then
        insert into public.nc_causas (nc_id, causa_id)
        select v_nc_id, causa_id
          from (select distinct pg_catalog.unnest(p_causa_ids) as causa_id) as causas_distintas;
    end if;

    insert into public.historico_nc (
        nc_id, usuario_id, status_anterior, status_novo, observacao, criado_em
    ) values (
        v_nc_id, p_aberto_por, null, 'aberta'::public.status_nc, 'NC aberta', v_agora
    );

    return pg_catalog.jsonb_build_object('ok', true, 'nc_id', v_nc_id, 'status', 'aberta');
end;
$$;

create or replace function public.validar_nc_com_workflow_v3(
    p_nc_id bigint,
    p_responsavel_id uuid
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_resultado jsonb;
    v_validado_em timestamptz;
begin
    v_resultado := public.validar_nc_com_ocorrencias_v2(p_nc_id, p_responsavel_id);

    if not coalesce((v_resultado ->> 'ok')::boolean, false) then
        return v_resultado;
    end if;

    select validado_em into v_validado_em
      from public.nao_conformidades
     where id = p_nc_id;

    insert into public.historico_nc (
        nc_id, usuario_id, status_anterior, status_novo, observacao, criado_em
    ) values (
        p_nc_id,
        p_responsavel_id,
        'aberta'::public.status_nc,
        'aguardando_feedback'::public.status_nc,
        'NC validada e disponibilizada para feedback',
        coalesce(v_validado_em, pg_catalog.now())
    );

    return v_resultado;
end;
$$;

create or replace function public.invalidar_nc_v3(
    p_nc_id bigint,
    p_responsavel_id uuid,
    p_motivo text
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_nc public.nao_conformidades%rowtype;
    v_agora timestamptz := pg_catalog.now();
    v_motivo text := pg_catalog.btrim(coalesce(p_motivo, ''));
begin
    select * into v_nc
      from public.nao_conformidades
     where id = p_nc_id
     for update;

    if not found then
        return pg_catalog.jsonb_build_object('ok', false, 'erro', 'nc_nao_encontrada');
    end if;
    if v_nc.status <> 'aberta'::public.status_nc then
        return pg_catalog.jsonb_build_object('ok', false, 'erro', 'nc_nao_aberta', 'status_atual', v_nc.status);
    end if;
    if v_motivo = '' then
        return pg_catalog.jsonb_build_object('ok', false, 'erro', 'motivo_ausente');
    end if;

    update public.nao_conformidades
       set responsavel_id = p_responsavel_id,
           status = 'invalidada'::public.status_nc,
           motivo_invalidacao = v_motivo,
           decidido_em = v_agora
     where id = p_nc_id;

    insert into public.historico_nc (
        nc_id, usuario_id, status_anterior, status_novo, observacao, criado_em
    ) values (
        p_nc_id, p_responsavel_id, 'aberta'::public.status_nc,
        'invalidada'::public.status_nc, v_motivo, v_agora
    );

    return pg_catalog.jsonb_build_object('ok', true, 'nc_id', p_nc_id, 'status', 'invalidada');
end;
$$;

create or replace function public.enviar_nc_legada_v3(
    p_nc_id bigint,
    p_responsavel_id uuid
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_nc public.nao_conformidades%rowtype;
    v_agora timestamptz := pg_catalog.now();
begin
    select * into v_nc
      from public.nao_conformidades
     where id = p_nc_id
     for update;

    if not found then
        return pg_catalog.jsonb_build_object('ok', false, 'erro', 'nc_nao_encontrada');
    end if;
    if v_nc.status <> 'validada'::public.status_nc then
        return pg_catalog.jsonb_build_object('ok', false, 'erro', 'status_invalido', 'status_atual', v_nc.status);
    end if;

    update public.nao_conformidades
       set status = 'aguardando_feedback'::public.status_nc,
           responsavel_id = coalesce(responsavel_id, p_responsavel_id),
           enviado_em = coalesce(enviado_em, v_agora)
     where id = p_nc_id;

    insert into public.historico_nc (
        nc_id, usuario_id, status_anterior, status_novo, observacao, criado_em
    ) values (
        p_nc_id, p_responsavel_id, 'validada'::public.status_nc,
        'aguardando_feedback'::public.status_nc,
        'NC legada avançada para o fluxo de feedback', v_agora
    );

    return pg_catalog.jsonb_build_object('ok', true, 'nc_id', p_nc_id, 'status', 'aguardando_feedback');
end;
$$;

create or replace function public.aplicar_feedback_nc_v3(
    p_nc_id bigint,
    p_responsavel_id uuid,
    p_feedback text
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_nc public.nao_conformidades%rowtype;
    v_agora timestamptz := pg_catalog.now();
    v_feedback text := pg_catalog.btrim(coalesce(p_feedback, ''));
begin
    select * into v_nc
      from public.nao_conformidades
     where id = p_nc_id
     for update;

    if not found then
        return pg_catalog.jsonb_build_object('ok', false, 'erro', 'nc_nao_encontrada');
    end if;
    if v_nc.status not in ('aguardando_feedback'::public.status_nc, 'aguardando_analise'::public.status_nc) then
        return pg_catalog.jsonb_build_object('ok', false, 'erro', 'status_invalido', 'status_atual', v_nc.status);
    end if;
    if v_feedback = '' then
        return pg_catalog.jsonb_build_object('ok', false, 'erro', 'feedback_ausente');
    end if;

    update public.nao_conformidades
       set status = 'aguardando_aceite'::public.status_nc,
           responsavel_id = p_responsavel_id,
           feedback = v_feedback,
           feedback_aplicado_em = v_agora
     where id = p_nc_id;

    insert into public.historico_nc (
        nc_id, usuario_id, status_anterior, status_novo, observacao, criado_em
    ) values (
        p_nc_id, p_responsavel_id, v_nc.status,
        'aguardando_aceite'::public.status_nc, 'Feedback aplicado', v_agora
    );

    return pg_catalog.jsonb_build_object('ok', true, 'nc_id', p_nc_id, 'status', 'aguardando_aceite');
end;
$$;

create or replace function public.aceitar_nc_v3(
    p_nc_id bigint,
    p_colaborador_id uuid,
    p_texto_aceite text
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_nc public.nao_conformidades%rowtype;
    v_agora timestamptz := pg_catalog.now();
begin
    select * into v_nc
      from public.nao_conformidades
     where id = p_nc_id
     for update;

    if not found then
        return pg_catalog.jsonb_build_object('ok', false, 'erro', 'nc_nao_encontrada');
    end if;
    if v_nc.colaborador_id is distinct from p_colaborador_id then
        return pg_catalog.jsonb_build_object('ok', false, 'erro', 'colaborador_incorreto');
    end if;
    if v_nc.status <> 'aguardando_aceite'::public.status_nc then
        return pg_catalog.jsonb_build_object('ok', false, 'erro', 'status_invalido', 'status_atual', v_nc.status);
    end if;

    update public.nao_conformidades
       set status = 'concluida'::public.status_nc,
           texto_aceite = p_texto_aceite,
           aceito_em = v_agora
     where id = p_nc_id;

    insert into public.historico_nc (
        nc_id, usuario_id, status_anterior, status_novo, observacao, criado_em
    ) values (
        p_nc_id, p_colaborador_id, 'aguardando_aceite'::public.status_nc,
        'concluida'::public.status_nc, 'Aceite formal do colaborador', v_agora
    );

    return pg_catalog.jsonb_build_object('ok', true, 'nc_id', p_nc_id, 'status', 'concluida');
end;
$$;

revoke all on function public.criar_nc_com_historico_v3(date, text, text, text, uuid, text, text, uuid, text, bigint[]) from public, anon, authenticated;
revoke all on function public.validar_nc_com_workflow_v3(bigint, uuid) from public, anon, authenticated;
revoke all on function public.invalidar_nc_v3(bigint, uuid, text) from public, anon, authenticated;
revoke all on function public.enviar_nc_legada_v3(bigint, uuid) from public, anon, authenticated;
revoke all on function public.aplicar_feedback_nc_v3(bigint, uuid, text) from public, anon, authenticated;
revoke all on function public.aceitar_nc_v3(bigint, uuid, text) from public, anon, authenticated;

grant execute on function public.criar_nc_com_historico_v3(date, text, text, text, uuid, text, text, uuid, text, bigint[]) to service_role;
grant execute on function public.validar_nc_com_workflow_v3(bigint, uuid) to service_role;
grant execute on function public.invalidar_nc_v3(bigint, uuid, text) to service_role;
grant execute on function public.enviar_nc_legada_v3(bigint, uuid) to service_role;
grant execute on function public.aplicar_feedback_nc_v3(bigint, uuid, text) to service_role;
grant execute on function public.aceitar_nc_v3(bigint, uuid, text) to service_role;

-- Backfill conservador dos timestamps de ciclo a partir da auditoria.
update public.nao_conformidades as nc
   set validado_em = (
       select h.criado_em from public.historico_nc as h
        where h.nc_id = nc.id
          and h.status_novo in ('validada'::public.status_nc, 'aguardando_feedback'::public.status_nc, 'aguardando_analise'::public.status_nc)
        order by h.criado_em, h.id limit 1
   )
 where nc.validado_em is null
   and nc.status in ('validada'::public.status_nc, 'aguardando_analise'::public.status_nc, 'aguardando_feedback'::public.status_nc, 'aguardando_aceite'::public.status_nc, 'concluida'::public.status_nc)
   and exists (
       select 1 from public.historico_nc as h
        where h.nc_id = nc.id
          and h.status_novo in ('validada'::public.status_nc, 'aguardando_feedback'::public.status_nc, 'aguardando_analise'::public.status_nc)
   );

update public.nao_conformidades as nc
   set feedback_aplicado_em = (
       select h.criado_em from public.historico_nc as h
        where h.nc_id = nc.id and h.status_novo = 'aguardando_aceite'::public.status_nc
        order by h.criado_em, h.id limit 1
   )
 where nc.feedback_aplicado_em is null
   and nc.status in ('aguardando_aceite'::public.status_nc, 'concluida'::public.status_nc)
   and exists (
       select 1 from public.historico_nc as h
        where h.nc_id = nc.id and h.status_novo = 'aguardando_aceite'::public.status_nc
   );

update public.nao_conformidades as nc
   set aceito_em = (
       select h.criado_em from public.historico_nc as h
        where h.nc_id = nc.id and h.status_novo = 'concluida'::public.status_nc
        order by h.criado_em, h.id limit 1
   )
 where nc.aceito_em is null
   and nc.status = 'concluida'::public.status_nc
   and exists (
       select 1 from public.historico_nc as h
        where h.nc_id = nc.id and h.status_novo = 'concluida'::public.status_nc
   );

commit;
