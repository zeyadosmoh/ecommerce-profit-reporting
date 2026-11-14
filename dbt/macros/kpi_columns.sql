{# Shared aggregate columns over int_profit_ledger rows (alias `e`) for the weekly marts. #}
{% macro ledger_kpis() %}
    count(distinct e.order_id) filter (where e.event_type = 'sale')                 as orders,
    coalesce(sum(e.units_sold), 0)                                                  as units_sold,
    coalesce(sum(e.units_refunded), 0)                                              as units_refunded,
    coalesce(sum(e.gross_revenue_ex_vat), 0)                                        as gross_sales_ex_vat,
    coalesce(sum(e.discount_ex_vat), 0)                                             as discounts_ex_vat,
    coalesce(sum(e.net_revenue_ex_vat) filter (where e.event_type = 'sale'), 0)     as net_sales_ex_vat,
    coalesce(sum(e.net_revenue_ex_vat), 0)                                          as net_revenue_ex_vat,
    coalesce(sum(e.refunds_ex_vat), 0)                                              as refunds_ex_vat,
    coalesce(sum(e.shipping_charged_ex_vat), 0)                                     as shipping_charged_ex_vat,
    coalesce(sum(e.shipping_cost_ex_vat), 0)                                        as shipping_cost_ex_vat,
    coalesce(sum(e.payment_fee), 0)                                                 as payment_fees,
    sum(e.cogs)                                                                     as cogs_known_cost,
    sum(e.gross_profit)                                                             as gross_profit_known_cost,
    sum(e.net_profit)                                                               as net_profit_known_cost,
    coalesce(sum(e.net_revenue_ex_vat) filter (where not e.cost_missing), 0)        as net_revenue_known_cost_ex_vat,
    round(100 * sum(e.net_profit)
          / nullif(sum(e.net_revenue_ex_vat) filter (where not e.cost_missing), 0), 2) as net_margin_pct_known_cost,
    count(distinct e.line_id) filter (where e.cost_missing and e.event_type = 'sale') as missing_cost_lines,
    coalesce(sum(e.net_revenue_ex_vat) filter (where e.cost_missing), 0)            as missing_cost_net_revenue_ex_vat
{% endmacro %}
