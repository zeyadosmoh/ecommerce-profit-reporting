-- Product x ISO week, from the profit ledger (refunds land in the week chosen by refund_week_basis).
select
    e.sku,
    p.product_id,
    p.product_name,
    p.category,
    e.event_week                    as week_start,
    e.event_week + 6                as week_end,
    {{ ledger_kpis() }}
from {{ ref('int_profit_ledger') }} as e
left join {{ ref('stg_products') }} as p on p.sku = e.sku
group by 1, 2, 3, 4, 5, 6
