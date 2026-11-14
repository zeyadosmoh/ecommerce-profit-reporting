{% set basis = var('refund_week_basis') %}
{% if basis not in ['refund_date', 'order_date'] %}
    {{ exceptions.raise_compiler_error("refund_week_basis must be 'refund_date' or 'order_date', got '" ~ basis ~ "'") }}
{% endif %}
-- Profit events per order line, each dated into a week:
--   'sale'   on the order date: revenue, discount, allocated shipping/fees, COGS;
--   'refund' on the refund date (or the order date, per var refund_week_basis): negative revenue,
--            negative shipping charged, COGS reversal. No fee or shipping-cost recovery.
-- Only revenue-status orders. Missing cost => cogs / profit NULL.
with lines as (
    select
        l.order_id, l.line_id, l.sku, l.quantity,
        l.gross_revenue_ex_vat, l.discount_ex_vat, l.net_revenue_ex_vat,
        o.order_date,
        lc.cost_missing, lc.cogs,
        a.shipping_charged_alloc, a.shipping_cost_alloc, a.payment_fee_alloc
    from {{ ref('stg_order_lines') }} as l
    join {{ ref('stg_orders') }} as o on o.order_id = l.order_id
    join {{ ref('int_line_costs') }} as lc on lc.order_id = l.order_id and lc.line_id = l.line_id
    join {{ ref('int_order_allocations') }} as a on a.order_id = l.order_id and a.line_id = l.line_id
    where o.is_revenue_status
),

events as (
    select
        'sale'::text                    as event_type,
        order_id, line_id,
        null::bigint                    as refund_id,
        sku,
        order_date,
        order_date                      as event_date,
        quantity                        as units_sold,
        0                               as units_refunded,
        gross_revenue_ex_vat,
        discount_ex_vat,
        net_revenue_ex_vat,
        0::numeric(12,2)                as refunds_ex_vat,
        shipping_charged_alloc          as shipping_charged_ex_vat,
        shipping_cost_alloc             as shipping_cost_ex_vat,
        payment_fee_alloc               as payment_fee,
        cogs,
        cost_missing
    from lines

    union all

    select
        'refund',
        l.order_id, l.line_id,
        r.refund_id,
        l.sku,
        l.order_date,
        {% if basis == 'refund_date' %}r.refund_date{% else %}l.order_date{% endif %},
        0,
        r.refunded_qty,
        0,
        0,
        -r.refunded_net_revenue_ex_vat,
        r.refunded_net_revenue_ex_vat + r.refunded_shipping_ex_vat,
        -r.refunded_shipping_ex_vat,
        0,
        0,
        case when not l.cost_missing then -r.cogs_reversed end,
        l.cost_missing
    from {{ ref('int_line_refunds') }} as r
    join lines as l on l.order_id = r.order_id and l.line_id = r.line_id
)

select
    *,
    {{ iso_week('event_date') }} as event_week,
    case when not cost_missing then net_revenue_ex_vat - cogs end as gross_profit,
    case when not cost_missing
         then net_revenue_ex_vat - cogs + shipping_charged_ex_vat - shipping_cost_ex_vat - payment_fee end as net_profit
from events
