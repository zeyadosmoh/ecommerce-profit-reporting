-- Store KPIs per ISO week (Monday-Sunday, store-local), from the profit ledger.
-- Columns form a bridge that adds up line by line (see macros/kpi_columns.sql); profit covers
-- known-cost lines only and the missing_cost_* columns show exactly what is excluded.
{% set nullable = ['cogs_known_cost', 'gross_profit_known_cost', 'net_profit_known_cost', 'net_margin_pct_known_cost'] %}
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
    w.week_start + 6                                                   as week_end,
    (w.week_start >= b.first_date and w.week_start + 6 <= b.last_date) as is_full_week,
    {% for c in kpi_column_names() -%}
    {% if c in nullable %}k.{{ c }}{% else %}coalesce(k.{{ c }}, 0) as {{ c }}{% endif %},
    {% endfor -%}
    round(k.net_sales_ex_vat / nullif(k.orders, 0), 2)                 as aov_ex_vat_products
from weeks as w
cross join bounds as b
left join kpis as k on k.week_start = w.week_start
