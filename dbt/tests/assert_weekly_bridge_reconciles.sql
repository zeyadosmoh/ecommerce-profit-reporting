-- Every week (agg_weekly_kpis) and every product-week (agg_weekly_kpis's sibling agg_product_weekly)
-- must add up line by line, exactly to the halala, as displayed in the report and profit-summary.
{% set checks = [
    ('gross_sales - discounts = net_sales',
     'gross_sales_ex_vat - discounts_ex_vat', 'net_sales_ex_vat'),
    ('net_sales - product_refunds = net_revenue',
     'net_sales_ex_vat - product_refunds_ex_vat', 'net_revenue_ex_vat'),
    ('product_refunds + shipping_refunds = refunds',
     'product_refunds_ex_vat + shipping_refunds_ex_vat', 'refunds_ex_vat'),
    ('known + excluded revenue = net_revenue',
     'net_revenue_known_cost_ex_vat + missing_cost_net_revenue_ex_vat', 'net_revenue_ex_vat'),
    ('net_revenue_known - cogs = gross_profit',
     'net_revenue_known_cost_ex_vat - coalesce(cogs_known_cost, 0)', 'coalesce(gross_profit_known_cost, 0)'),
    ('gross_profit + shipping charged - shipping refunded - shipping cost - fees = net_profit',
     'coalesce(gross_profit_known_cost, 0) + shipping_charged_known_cost_ex_vat - shipping_refunds_known_cost_ex_vat'
       ~ ' - shipping_cost_known_cost_ex_vat - payment_fees_known_cost', 'coalesce(net_profit_known_cost, 0)'),
    ('known + excluded shipping charged = store',
     'shipping_charged_known_cost_ex_vat + missing_cost_shipping_charged_ex_vat', 'shipping_charged_ex_vat'),
    ('known + excluded shipping refunds = store',
     'shipping_refunds_known_cost_ex_vat + missing_cost_shipping_refunds_ex_vat', 'shipping_refunds_ex_vat'),
    ('known + excluded shipping cost = store',
     'shipping_cost_known_cost_ex_vat + missing_cost_shipping_cost_ex_vat', 'shipping_cost_ex_vat'),
    ('known + excluded payment fees = store',
     'payment_fees_known_cost + missing_cost_payment_fees', 'payment_fees'),
] %}
{% for model, key in [('agg_weekly_kpis', 'week_start::text'), ('agg_product_weekly', "sku || ' ' || week_start")] %}
{% set outer = loop %}
{% for name, lhs, rhs in checks %}
select '{{ model }}' as model, {{ key }} as row_key, '{{ name }}' as check_name,
       ({{ lhs }})::numeric as lhs, ({{ rhs }})::numeric as rhs
from {{ ref(model) }}
where ({{ lhs }}) is distinct from ({{ rhs }})
{% if not (loop.last and outer.last) %}union all{% endif %}
{% endfor %}
{% endfor %}
