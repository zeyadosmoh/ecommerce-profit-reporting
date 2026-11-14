-- Order-level shipping charged (ex-VAT), shipping cost and payment fee allocated to lines
-- in proportion to line net revenue, using largest-remainder rounding (sums are exact).
-- A NULL shipping cost / fee in the source allocates as 0 and is reported in dq_issues.
with lines as (
    select
        l.order_id,
        l.line_id,
        l.net_revenue_ex_vat,
        o.shipping_charged_ex_vat,
        o.shipping_cost_ex_vat,
        o.payment_fee
    from {{ ref('stg_order_lines') }} as l
    join {{ ref('stg_orders') }} as o on o.order_id = l.order_id
)

select
    lines.order_id,
    lines.line_id,
    sc.shipping_charged_alloc,
    sk.shipping_cost_alloc,
    pf.payment_fee_alloc
from lines
join {{ allocate_largest_remainder('lines', 'order_id', 'line_id', 'net_revenue_ex_vat', 'shipping_charged_ex_vat', 'shipping_charged_alloc') }} as sc
    on sc.order_id = lines.order_id and sc.line_id = lines.line_id
join {{ allocate_largest_remainder('lines', 'order_id', 'line_id', 'net_revenue_ex_vat', 'shipping_cost_ex_vat', 'shipping_cost_alloc') }} as sk
    on sk.order_id = lines.order_id and sk.line_id = lines.line_id
join {{ allocate_largest_remainder('lines', 'order_id', 'line_id', 'net_revenue_ex_vat', 'payment_fee', 'payment_fee_alloc') }} as pf
    on pf.order_id = lines.order_id and pf.line_id = lines.line_id
