-- Whole-period totals of the weekly marts must equal the line fact (built independently of the ledger),
-- for every amount, to the halala.
with lines as (
    select
        sum(quantity)                                   as units_sold,
        sum(refunded_qty)                               as units_refunded,
        sum(discount_ex_vat)                            as discounts,
        sum(net_revenue_ex_vat)                         as net_sales,
        sum(net_revenue_after_refunds_ex_vat)           as net_revenue,
        sum(refunds_ex_vat)                             as refunds,
        sum(shipping_charged_after_refunds_ex_vat)      as shipping_charged,
        sum(shipping_cost_ex_vat)                       as shipping_cost,
        sum(payment_fee)                                as payment_fees,
        sum(net_cogs)                                   as cogs,
        sum(gross_profit)                               as gross_profit,
        sum(net_profit)                                 as net_profit,
        sum(net_revenue_after_refunds_ex_vat) filter (where cost_missing) as missing_cost_revenue,
        count(*) filter (where cost_missing)            as missing_cost_lines,
        count(distinct order_id)                        as orders
    from {{ ref('fct_order_lines_profit') }}
),

{% for mart in ['agg_weekly_kpis', 'agg_product_weekly'] %}
{{ mart }}_totals as (
    select
        sum(units_sold) as units_sold, sum(units_refunded) as units_refunded, sum(discounts_ex_vat) as discounts,
        sum(net_sales_ex_vat) as net_sales, sum(net_revenue_ex_vat) as net_revenue, sum(refunds_ex_vat) as refunds,
        sum(shipping_charged_ex_vat) as shipping_charged, sum(shipping_cost_ex_vat) as shipping_cost,
        sum(payment_fees) as payment_fees, sum(cogs_known_cost) as cogs,
        sum(gross_profit_known_cost) as gross_profit, sum(net_profit_known_cost) as net_profit,
        sum(missing_cost_net_revenue_ex_vat) as missing_cost_revenue, sum(missing_cost_lines) as missing_cost_lines,
        sum(orders) as orders
    from {{ ref(mart) }}
){% if not loop.last %},{% endif %}
{% endfor %}

{% set cols = ['units_sold', 'units_refunded', 'discounts', 'net_sales', 'net_revenue', 'refunds',
               'shipping_charged', 'shipping_cost', 'payment_fees', 'cogs', 'gross_profit', 'net_profit',
               'missing_cost_revenue', 'missing_cost_lines', 'orders'] %}
{# product-weekly counts an order once per product, so its order count is not comparable #}
{% set checks = [] %}
{% for mart in ['agg_weekly_kpis', 'agg_product_weekly'] %}{% for c in cols %}
{% if not (mart == 'agg_product_weekly' and c == 'orders') %}{% do checks.append((mart, c)) %}{% endif %}
{% endfor %}{% endfor %}

{% for mart, c in checks %}
select '{{ mart }}' as mart, '{{ c }}' as metric, l.{{ c }}::numeric as from_lines, m.{{ c }}::numeric as from_mart
from lines as l cross join {{ mart }}_totals as m
where l.{{ c }} is distinct from m.{{ c }}
{% if not loop.last %}union all{% endif %}
{% endfor %}
