{# Shared aggregate columns over int_profit_ledger rows (alias `e`) for the weekly marts.
   Every amount is built so that a reader can add the bridge up line by line:
     gross_sales - discounts = net_sales;  net_sales - product_refunds = net_revenue
     net_revenue = net_revenue_known_cost + missing_cost_net_revenue
     net_revenue_known_cost - cogs_known_cost = gross_profit_known_cost
     gross_profit_known_cost + shipping_charged_known_cost - shipping_refunds_known_cost
       - shipping_cost_known_cost - payment_fees_known_cost = net_profit_known_cost
     store totals (shipping charged / refunded / cost / fees) = known_cost + missing_cost parts #}
{% macro kpi_column_names() %}
{{ return(['orders', 'units_sold', 'units_refunded',
           'gross_sales_ex_vat', 'discounts_ex_vat', 'net_sales_ex_vat', 'product_refunds_ex_vat',
           'net_revenue_ex_vat', 'shipping_charged_ex_vat', 'shipping_refunds_ex_vat', 'refunds_ex_vat',
           'shipping_cost_ex_vat', 'payment_fees',
           'net_revenue_known_cost_ex_vat', 'cogs_known_cost', 'gross_profit_known_cost',
           'shipping_charged_known_cost_ex_vat', 'shipping_refunds_known_cost_ex_vat',
           'shipping_cost_known_cost_ex_vat', 'payment_fees_known_cost', 'net_profit_known_cost',
           'net_margin_pct_known_cost',
           'missing_cost_lines', 'missing_cost_net_revenue_ex_vat', 'missing_cost_shipping_charged_ex_vat',
           'missing_cost_shipping_refunds_ex_vat', 'missing_cost_shipping_cost_ex_vat', 'missing_cost_payment_fees']) }}
{% endmacro %}

{% macro ledger_kpis() %}
    {%- set sale = "e.event_type = 'sale'" -%}
    {%- set refund = "e.event_type = 'refund'" -%}
    count(distinct e.order_id) filter (where {{ sale }})                                          as orders,
    coalesce(sum(e.units_sold), 0)                                                                as units_sold,
    coalesce(sum(e.units_refunded), 0)                                                            as units_refunded,
    -- revenue, all lines
    coalesce(sum(e.gross_revenue_ex_vat), 0)                                                      as gross_sales_ex_vat,
    coalesce(sum(e.discount_ex_vat), 0)                                                           as discounts_ex_vat,
    coalesce(sum(e.net_revenue_ex_vat) filter (where {{ sale }}), 0)                              as net_sales_ex_vat,
    coalesce(-sum(e.net_revenue_ex_vat) filter (where {{ refund }}), 0)                           as product_refunds_ex_vat,
    coalesce(sum(e.net_revenue_ex_vat), 0)                                                        as net_revenue_ex_vat,
    coalesce(sum(e.shipping_charged_ex_vat) filter (where {{ sale }}), 0)                         as shipping_charged_ex_vat,
    coalesce(-sum(e.shipping_charged_ex_vat) filter (where {{ refund }}), 0)                      as shipping_refunds_ex_vat,
    coalesce(sum(e.refunds_ex_vat), 0)                                                            as refunds_ex_vat,
    coalesce(sum(e.shipping_cost_ex_vat), 0)                                                      as shipping_cost_ex_vat,
    coalesce(sum(e.payment_fee), 0)                                                               as payment_fees,
    -- known-cost profit bridge
    coalesce(sum(e.net_revenue_ex_vat) filter (where not e.cost_missing), 0)                      as net_revenue_known_cost_ex_vat,
    sum(e.cogs)                                                                                   as cogs_known_cost,
    sum(e.gross_profit)                                                                           as gross_profit_known_cost,
    coalesce(sum(e.shipping_charged_ex_vat) filter (where {{ sale }} and not e.cost_missing), 0)  as shipping_charged_known_cost_ex_vat,
    coalesce(-sum(e.shipping_charged_ex_vat) filter (where {{ refund }} and not e.cost_missing), 0) as shipping_refunds_known_cost_ex_vat,
    coalesce(sum(e.shipping_cost_ex_vat) filter (where not e.cost_missing), 0)                    as shipping_cost_known_cost_ex_vat,
    coalesce(sum(e.payment_fee) filter (where not e.cost_missing), 0)                             as payment_fees_known_cost,
    sum(e.net_profit)                                                                             as net_profit_known_cost,
    round(100 * sum(e.net_profit)
          / nullif(sum(e.net_revenue_ex_vat) filter (where not e.cost_missing), 0), 2)            as net_margin_pct_known_cost,
    -- excluded: missing-cost lines
    count(distinct e.line_id) filter (where e.cost_missing and {{ sale }})                        as missing_cost_lines,
    coalesce(sum(e.net_revenue_ex_vat) filter (where e.cost_missing), 0)                          as missing_cost_net_revenue_ex_vat,
    coalesce(sum(e.shipping_charged_ex_vat) filter (where {{ sale }} and e.cost_missing), 0)      as missing_cost_shipping_charged_ex_vat,
    coalesce(-sum(e.shipping_charged_ex_vat) filter (where {{ refund }} and e.cost_missing), 0)   as missing_cost_shipping_refunds_ex_vat,
    coalesce(sum(e.shipping_cost_ex_vat) filter (where e.cost_missing), 0)                        as missing_cost_shipping_cost_ex_vat,
    coalesce(sum(e.payment_fee) filter (where e.cost_missing), 0)                                 as missing_cost_payment_fees
{% endmacro %}
