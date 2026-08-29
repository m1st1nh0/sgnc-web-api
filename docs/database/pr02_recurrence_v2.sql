-- PR02 - Reincidência V2
--
-- Fonte de verdade: public.nc_causas.ocorrencia_numero.
-- Regra: mesmo colaborador + mesma causa + janela móvel de 12 meses de
-- calendário, contando apenas NCs procedentes/validadas.
--
-- A validação e a atribuição dos números de ocorrência acontecem na mesma
-- transação. Advisory locks por colaborador+causa serializam validações
-- concorrentes e impedem duas NCs de receberem o mesmo número.

begin;

-- A consulta de histórico da RPC começa por colaborador e intervalo de data.
create index if not exists idx_nc_reincidencia_v2
    on public.nao_conformidades (colaborador_id, data, id)
    where status in (
        'validada'::public.status_nc,
        'aguardando_analise'::public.status_nc,
        'aguardando_feedback'::public.status_nc,
        'aguardando_aceite'::public.status_nc,
        'concluida'::public.status_nc
    );

comment on column public.nc_causas.ocorrencia_numero is
    'Snapshot da ocorrência da causa para o colaborador na janela móvel de 12 meses no momento da validação. Fonte canônica da reincidência.';

create or replace function public.validar_nc_com_ocorrencias_v2(
    p_nc_id bigint,
    p_responsavel_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_nc public.nao_conformidades%rowtype;
    v_causa record;
    v_numero integer;
    v_referencia date;
    v_agora timestamptz;
    v_reincidencia text := 'Não';
    v_ocorrencias jsonb := '[]'::jsonb;
begin
    -- Serializa qualquer tentativa de avaliar a mesma NC.
    select *
      into v_nc
      from public.nao_conformidades
     where id = p_nc_id
     for update;

    if not found then
        return pg_catalog.jsonb_build_object(
            'ok', false,
            'erro', 'nc_nao_encontrada'
        );
    end if;

    if v_nc.status <> 'aberta'::public.status_nc then
        return pg_catalog.jsonb_build_object(
            'ok', false,
            'erro', 'nc_nao_aberta',
            'status_atual', v_nc.status
        );
    end if;

    if v_nc.colaborador_id is null then
        return pg_catalog.jsonb_build_object(
            'ok', false,
            'erro', 'colaborador_ausente'
        );
    end if;

    v_referencia := v_nc.data;

    -- A ordem fixa de causa evita deadlock quando duas NCs compartilham
    -- múltiplas causas e são validadas simultaneamente.
    for v_causa in
        select rel.causa_id
          from public.nc_causas as rel
         where rel.nc_id = p_nc_id
         order by rel.causa_id
    loop
        -- Hash collision só causaria serialização adicional, nunca numeração
        -- incorreta. O lock é liberado automaticamente no fim da transação.
        perform pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended(
                v_nc.colaborador_id::text || ':' || v_causa.causa_id::text,
                0
            )
        );

        select pg_catalog.count(*)::integer + 1
          into v_numero
          from public.nao_conformidades as anterior
          join public.nc_causas as rel_anterior
            on rel_anterior.nc_id = anterior.id
           and rel_anterior.causa_id = v_causa.causa_id
         where anterior.colaborador_id = v_nc.colaborador_id
           and anterior.id <> p_nc_id
           and anterior.status in (
                'validada'::public.status_nc,
                'aguardando_analise'::public.status_nc,
                'aguardando_feedback'::public.status_nc,
                'aguardando_aceite'::public.status_nc,
                'concluida'::public.status_nc
           )
           and anterior.data >= (v_referencia - interval '12 months')::date
           and anterior.data <= v_referencia;

        update public.nc_causas
           set ocorrencia_numero = v_numero
         where nc_id = p_nc_id
           and causa_id = v_causa.causa_id;

        if v_numero > 1 then
            v_reincidencia := 'Sim';
        end if;

        v_ocorrencias := v_ocorrencias || pg_catalog.jsonb_build_array(
            pg_catalog.jsonb_build_object(
                'causa_id', v_causa.causa_id,
                'ocorrencia_numero', v_numero
            )
        );
    end loop;

    v_agora := pg_catalog.now();

    update public.nao_conformidades
       set responsavel_id = p_responsavel_id,
           status = 'aguardando_feedback'::public.status_nc,
           validado_em = v_agora,
           enviado_em = v_agora,
           decidido_em = v_agora,
           reincidencia = v_reincidencia
     where id = p_nc_id;

    return pg_catalog.jsonb_build_object(
        'ok', true,
        'nc_id', p_nc_id,
        'status', 'aguardando_feedback',
        'reincidencia', v_reincidencia,
        'ocorrencias', v_ocorrencias
    );
end;
$$;

-- SECURITY DEFINER em schema exposto só é aceitável porque a RPC é fechada
-- para navegador/usuário e liberada exclusivamente ao service_role do backend.
revoke all on function public.validar_nc_com_ocorrencias_v2(bigint, uuid)
    from public, anon, authenticated;
grant execute on function public.validar_nc_com_ocorrencias_v2(bigint, uuid)
    to service_role;

-- =============================================================
-- Backfill histórico
-- =============================================================
-- Para linhas já procedentes, preenche o snapshot que estava nulo. Em datas
-- iguais, validado_em/criado_em/id dão uma ordenação determinística.
with relacoes_validas as (
    select
        rel.nc_id,
        rel.causa_id,
        nc.colaborador_id,
        nc.data,
        coalesce(nc.validado_em, nc.criado_em) as momento
    from public.nc_causas as rel
    join public.nao_conformidades as nc on nc.id = rel.nc_id
    where nc.colaborador_id is not null
      and nc.status in (
        'validada'::public.status_nc,
        'aguardando_analise'::public.status_nc,
        'aguardando_feedback'::public.status_nc,
        'aguardando_aceite'::public.status_nc,
        'concluida'::public.status_nc
      )
), calculado as (
    select
        atual.nc_id,
        atual.causa_id,
        pg_catalog.count(anterior.nc_id)::integer + 1 as ocorrencia_numero
    from relacoes_validas as atual
    left join relacoes_validas as anterior
      on anterior.colaborador_id = atual.colaborador_id
     and anterior.causa_id = atual.causa_id
     and anterior.nc_id <> atual.nc_id
     and anterior.data >= (atual.data - interval '12 months')::date
     and anterior.data <= atual.data
     and (
        anterior.data < atual.data
        or (
            anterior.data = atual.data
            and (anterior.momento, anterior.nc_id) < (atual.momento, atual.nc_id)
        )
     )
    group by atual.nc_id, atual.causa_id
)
update public.nc_causas as rel
   set ocorrencia_numero = calculado.ocorrencia_numero
  from calculado
 where rel.nc_id = calculado.nc_id
   and rel.causa_id = calculado.causa_id;

-- Mantém o booleano textual apenas como projeção legada da regra canônica.
update public.nao_conformidades as nc
   set reincidencia = case
       when exists (
           select 1
             from public.nc_causas as rel
            where rel.nc_id = nc.id
              and rel.ocorrencia_numero > 1
       ) then 'Sim'
       else 'Não'
   end
 where nc.status in (
    'validada'::public.status_nc,
    'aguardando_analise'::public.status_nc,
    'aguardando_feedback'::public.status_nc,
    'aguardando_aceite'::public.status_nc,
    'concluida'::public.status_nc
 );

-- Enquanto aberta/invalidada não existe ocorrência procedente ainda.
update public.nao_conformidades
   set reincidencia = 'Não'
 where status in ('aberta'::public.status_nc, 'invalidada'::public.status_nc);

commit;
