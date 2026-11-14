-- Effective-dated cost per order line: the cost row with the latest effective_from <= order_date.
-- No matching row => cost_missing = true and cogs is NULL (never zero).
select
    l.order_id,
    l.line_id,
    l.sku,
    l.quantity,
    o.order_date,
    c.effective_from                     as cost_effective_from,
    c.unit_cost,
    c.packaging_cost,
    c.unit_landed_cost,
    c.effective_from is null             as cost_missing,
    case when c.effective_from is not null then c.unit_landed_cost * l.quantity end as cogs
from {{ ref('stg_order_lines') }} as l
join {{ ref('stg_orders') }} as o on o.order_id = l.order_id
left join {{ ref('stg_costs') }} as c
    on  c.sku = l.sku
    and c.effective_from <= o.order_date
    and (c.next_effective_from is null or o.order_date < c.next_effective_from)
