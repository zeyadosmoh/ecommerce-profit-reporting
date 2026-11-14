-- 1. Every costed line uses a cost row with effective_from <= order date, and no other row of the same
--    SKU sits between that row and the order date (i.e. it really is the latest applicable row).
-- 2. No line is flagged missing while an applicable cost row exists.
-- 3. The data actually exercises a cost change: at least one SKU has costed lines on BOTH sides of a
--    later effective_from, with different unit costs before and after.
with lines as (
    select * from {{ ref('fct_order_lines_profit') }}
),

wrong_row as (
    select l.line_id, 'not the latest applicable cost row' as problem
    from lines as l
    where not l.cost_missing
      and (l.cost_effective_from > l.order_date
           or exists (select 1 from {{ source('raw', 'raw_costs') }} as c
                      where c.sku = l.sku and c.effective_from > l.cost_effective_from
                        and c.effective_from <= l.order_date)
           or l.unit_cost is distinct from (select c.unit_cost from {{ source('raw', 'raw_costs') }} as c
                                            where c.sku = l.sku and c.effective_from = l.cost_effective_from))
),

false_missing as (
    select l.line_id, 'flagged missing but a cost row applies'
    from lines as l
    where l.cost_missing
      and exists (select 1 from {{ source('raw', 'raw_costs') }} as c
                  where c.sku = l.sku and c.effective_from <= l.order_date)
),

changes as (
    select c.sku, c.effective_from
    from {{ source('raw', 'raw_costs') }} as c
    where exists (select 1 from {{ source('raw', 'raw_costs') }} as e
                  where e.sku = c.sku and e.effective_from < c.effective_from)
),

exercised as (
    select ch.sku
    from changes as ch
    join lines as l on l.sku = ch.sku and not l.cost_missing
    group by ch.sku, ch.effective_from
    having count(*) filter (where l.order_date <  ch.effective_from) > 0
       and count(*) filter (where l.order_date >= ch.effective_from) > 0
       and max(l.unit_cost) filter (where l.order_date <  ch.effective_from)
           <> max(l.unit_cost) filter (where l.order_date >= ch.effective_from)
)

select line_id, problem from wrong_row
union all select * from false_missing
union all select null, 'no SKU exercises a cost change on both sides' where not exists (select 1 from exercised)
