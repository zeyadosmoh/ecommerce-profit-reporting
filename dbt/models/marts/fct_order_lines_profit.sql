-- Lifetime profit per order line (revenue-status orders only), refunds included regardless of week.
-- Built from the intermediate models directly (not from the ledger) so the weekly tie-out test is meaningful.
-- cost_missing lines: cogs / gross_profit / net_profit are NULL, never zero.
with refunds as (
    select
        line_id,
        sum(refunded_qty)                  as refunded_qty,
        sum(refunded_net_revenue_ex_vat)   as refunded_net_revenue_ex_vat,
        sum(refunded_shipping_ex_vat)      as refunded_shipping_ex_vat,
        sum(cogs_reversed)                 as cogs_reversed,
        max(refund_date)                   as last_refund_date
    from {{ ref('int_line_refunds') }}
    group by 1
),

base as (
    select
        l.order_id,
        l.line_id,
        l.product_id,
        l.sku,
        coalesce(p.product_name, l.product_name)              as product_name,
        p.category,
        o.order_status,
        o.order_date,
        o.order_week,
        l.quantity,
        l.gross_revenue_ex_vat,
        l.discount_ex_vat,
        l.net_revenue_ex_vat,
        lc.cost_missing,
        lc.cost_effective_from,
        lc.unit_cost,
        lc.packaging_cost,
        lc.cogs,
        a.shipping_charged_alloc                               as shipping_charged_ex_vat,
        a.shipping_cost_alloc                                  as shipping_cost_ex_vat,
        a.payment_fee_alloc                                    as payment_fee,
        coalesce(r.refunded_qty, 0)                            as refunded_qty,
        coalesce(r.refunded_net_revenue_ex_vat, 0)             as refunded_net_revenue_ex_vat,
        coalesce(r.refunded_shipping_ex_vat, 0)                as refunded_shipping_ex_vat,
        case when not lc.cost_missing then coalesce(r.cogs_reversed, 0) end as cogs_reversed,
        r.last_refund_date
    from {{ ref('stg_order_lines') }} as l
    join {{ ref('stg_orders') }} as o on o.order_id = l.order_id
    join {{ ref('int_line_costs') }} as lc on lc.line_id = l.line_id
    join {{ ref('int_order_allocations') }} as a on a.line_id = l.line_id
    left join refunds as r on r.line_id = l.line_id
    left join {{ ref('stg_products') }} as p on p.product_id = l.product_id
    where o.is_revenue_status
)

select
    *,
    net_revenue_ex_vat - refunded_net_revenue_ex_vat                    as net_revenue_after_refunds_ex_vat,
    refunded_net_revenue_ex_vat + refunded_shipping_ex_vat              as refunds_ex_vat,
    shipping_charged_ex_vat - refunded_shipping_ex_vat                  as shipping_charged_after_refunds_ex_vat,
    cogs - cogs_reversed                                                as net_cogs,
    (net_revenue_ex_vat - refunded_net_revenue_ex_vat) - (cogs - cogs_reversed) as gross_profit,
    (net_revenue_ex_vat - refunded_net_revenue_ex_vat) - (cogs - cogs_reversed)
        + (shipping_charged_ex_vat - refunded_shipping_ex_vat)
        - shipping_cost_ex_vat - payment_fee                            as net_profit
from base
