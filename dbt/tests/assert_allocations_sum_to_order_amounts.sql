-- Every allocated order-level amount sums back to the order amount exactly (to the halala),
-- and every refunded-shipping allocation sums back to its refund's shipping amount.
with by_order as (
    select order_id,
           sum(shipping_charged_alloc) as shipping_charged,
           sum(shipping_cost_alloc)    as shipping_cost,
           sum(payment_fee_alloc)      as payment_fee
    from {{ ref('int_order_allocations') }}
    group by 1
),

by_refund as (
    select refund_id, sum(refunded_shipping_ex_vat) as refunded_shipping
    from {{ ref('int_line_refunds') }}
    group by 1
)

select 'order' as kind, o.order_id as id
from {{ ref('stg_orders') }} as o
join by_order as a on a.order_id = o.order_id
where a.shipping_charged <> o.shipping_charged_ex_vat
   or a.shipping_cost    <> coalesce(o.shipping_cost_ex_vat, 0)
   or a.payment_fee      <> coalesce(o.payment_fee, 0)

union all

select 'refund', r.refund_id
from {{ ref('stg_refunds') }} as r
left join by_refund as b on b.refund_id = r.refund_id
where r.refunded_shipping_ex_vat <> coalesce(b.refunded_shipping, 0)
