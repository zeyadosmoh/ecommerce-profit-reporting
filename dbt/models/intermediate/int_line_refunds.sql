-- One row per (refund, order line) with the refund's effect on that line:
--   refunded units and line revenue (ex-VAT) come from the refund's line items;
--   refunded shipping (ex-VAT) is allocated across the order's lines like shipping charged;
--   refunded units return to stock, so their COGS is reversed at the cost used on the original sale.
-- Payment fees and shipping cost are not recovered on refunds (nothing here touches them).
with refund_items as (
    select refund_id, order_id, line_id,
           sum(refunded_qty)                as refunded_qty,
           sum(refunded_net_revenue_ex_vat) as refunded_net_revenue_ex_vat
    from {{ ref('stg_refund_lines') }}
    group by 1, 2, 3
),

shipping_refund_lines as (
    select r.refund_id, l.line_id, l.net_revenue_ex_vat, r.refunded_shipping_ex_vat
    from {{ ref('stg_refunds') }} as r
    join {{ ref('stg_order_lines') }} as l on l.order_id = r.order_id
    where r.refunded_shipping_ex_vat <> 0
),

shipping_alloc as (
    select *
    from {{ allocate_largest_remainder('shipping_refund_lines', 'refund_id', 'line_id', 'net_revenue_ex_vat', 'refunded_shipping_ex_vat', 'refunded_shipping_alloc') }} as a
),

refund_line_keys as (
    select refund_id, line_id from refund_items
    union
    select refund_id, line_id from shipping_alloc
)

select
    k.refund_id,
    r.order_id,
    k.line_id,
    r.refund_date,
    r.refunded_at,
    coalesce(ri.refunded_qty, 0)                     as refunded_qty,
    coalesce(ri.refunded_net_revenue_ex_vat, 0)      as refunded_net_revenue_ex_vat,
    coalesce(sa.refunded_shipping_alloc, 0)          as refunded_shipping_ex_vat,
    lc.cost_missing,
    case when lc.cost_missing = false
         then coalesce(ri.refunded_qty, 0) * lc.unit_landed_cost end as cogs_reversed
from refund_line_keys as k
join {{ ref('stg_refunds') }} as r on r.refund_id = k.refund_id
left join refund_items as ri on ri.refund_id = k.refund_id and ri.line_id = k.line_id
left join shipping_alloc as sa on sa.refund_id = k.refund_id and sa.line_id = k.line_id
left join {{ ref('int_line_costs') }} as lc on lc.line_id = k.line_id
