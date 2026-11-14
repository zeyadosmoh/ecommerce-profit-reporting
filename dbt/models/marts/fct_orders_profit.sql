-- Lifetime profit per order (revenue-status orders). If any line lacks a cost, the order's
-- gross_profit / net_profit are NULL; the *_known_cost columns cover the lines that do have a cost.
select
    o.order_id,
    o.order_status,
    o.order_date,
    o.order_week,
    o.coupon_codes,
    o.payment_method,
    o.billing_city,
    count(*)                                                    as line_count,
    sum(f.quantity)                                             as units_sold,
    sum(f.refunded_qty)                                         as units_refunded,
    sum(f.gross_revenue_ex_vat)                                 as gross_revenue_ex_vat,
    sum(f.discount_ex_vat)                                      as discount_ex_vat,
    sum(f.net_revenue_ex_vat)                                   as net_revenue_ex_vat,
    sum(f.refunds_ex_vat)                                       as refunds_ex_vat,
    sum(f.net_revenue_after_refunds_ex_vat)                     as net_revenue_after_refunds_ex_vat,
    sum(f.shipping_charged_ex_vat)                              as shipping_charged_ex_vat,
    sum(f.shipping_charged_after_refunds_ex_vat)                as shipping_charged_after_refunds_ex_vat,
    sum(f.shipping_cost_ex_vat)                                 as shipping_cost_ex_vat,
    sum(f.payment_fee)                                          as payment_fee,
    bool_or(f.cost_missing)                                     as has_missing_cost,
    count(*) filter (where f.cost_missing)                      as missing_cost_lines,
    coalesce(sum(f.net_revenue_after_refunds_ex_vat) filter (where f.cost_missing), 0) as missing_cost_net_revenue_ex_vat,
    case when not bool_or(f.cost_missing) then sum(f.net_cogs) end      as net_cogs,
    case when not bool_or(f.cost_missing) then sum(f.gross_profit) end  as gross_profit,
    case when not bool_or(f.cost_missing) then sum(f.net_profit) end    as net_profit,
    sum(f.net_profit)                                           as net_profit_known_cost,
    round(100 * sum(f.net_profit)
          / nullif(sum(f.net_revenue_after_refunds_ex_vat) filter (where not f.cost_missing), 0), 2)
                                                                as net_margin_pct_known_cost
from {{ ref('stg_orders') }} as o
join {{ ref('fct_order_lines_profit') }} as f on f.order_id = o.order_id
group by 1, 2, 3, 4, 5, 6, 7
