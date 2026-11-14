-- Store KPIs per ISO week (Monday-Sunday, store-local), from the profit ledger.
-- Profit and margin cover known-cost lines only; missing_cost_* shows what is excluded.
with bounds as (
    select min(order_date) as first_date, max(order_date) as last_date
    from {{ ref('stg_orders') }}
),

weeks as (
    select generate_series({{ iso_week('first_date') }}, {{ iso_week('last_date') }}, interval '1 week')::date as week_start
    from bounds
),

kpis as (
    select e.event_week as week_start, {{ ledger_kpis() }}
    from {{ ref('int_profit_ledger') }} as e
    group by 1
)

select
    w.week_start,
    w.week_start + 6                                                  as week_end,
    (w.week_start >= b.first_date and w.week_start + 6 <= b.last_date) as is_full_week,
    coalesce(k.orders, 0)                           as orders,
    coalesce(k.units_sold, 0)                       as units_sold,
    coalesce(k.units_refunded, 0)                   as units_refunded,
    coalesce(k.gross_sales_ex_vat, 0)               as gross_sales_ex_vat,
    coalesce(k.discounts_ex_vat, 0)                 as discounts_ex_vat,
    coalesce(k.net_sales_ex_vat, 0)                 as net_sales_ex_vat,
    coalesce(k.refunds_ex_vat, 0)                   as refunds_ex_vat,
    coalesce(k.net_revenue_ex_vat, 0)               as net_revenue_ex_vat,
    k.cogs_known_cost,
    k.gross_profit_known_cost,
    coalesce(k.shipping_charged_ex_vat, 0)          as shipping_charged_ex_vat,
    coalesce(k.shipping_cost_ex_vat, 0)             as shipping_cost_ex_vat,
    coalesce(k.payment_fees, 0)                     as payment_fees,
    k.net_profit_known_cost,
    coalesce(k.net_revenue_known_cost_ex_vat, 0)    as net_revenue_known_cost_ex_vat,
    k.net_margin_pct_known_cost,
    round(k.net_sales_ex_vat / nullif(k.orders, 0), 2) as aov_ex_vat_products,
    coalesce(k.missing_cost_lines, 0)               as missing_cost_lines,
    coalesce(k.missing_cost_net_revenue_ex_vat, 0)  as missing_cost_net_revenue_ex_vat
from weeks as w
cross join bounds as b
left join kpis as k on k.week_start = w.week_start
