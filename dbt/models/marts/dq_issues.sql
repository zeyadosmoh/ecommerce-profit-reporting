-- Data-quality issues that affect profit reporting, one row per issue.
with excluded_orders as (
    select
        'excluded_order_status'::text      as issue_type,
        'info'::text                       as severity,
        'order'::text                      as entity,
        order_id::text                     as source_id,
        null::text                         as sku,
        order_week                         as issue_week,
        total_ex_vat                       as amount_ex_vat,
        'status ' || order_status || ' is not a revenue status' as detail
    from {{ ref('stg_orders') }}
    where not is_revenue_status
),

missing_costs as (
    select
        'missing_cost', 'warning', 'order_line',
        order_id || '-' || line_id,
        sku,
        order_week,
        net_revenue_after_refunds_ex_vat,
        'no cost row for sku ' || sku || ' effective on ' || order_date || '; excluded from profit'
    from {{ ref('fct_order_lines_profit') }}
    where cost_missing
),

missing_order_meta as (
    select
        'missing_order_cost_meta', 'warning', 'order',
        order_id::text,
        null,
        order_week,
        total_ex_vat,
        concat_ws(', ',
            case when shipping_cost_ex_vat is null then '_shipping_cost missing' end,
            case when payment_fee is null then '_payment_gateway_fee missing' end) || '; treated as 0'
    from {{ ref('stg_orders') }}
    where is_revenue_status and (shipping_cost_ex_vat is null or payment_fee is null)
),

orphan_refunds as (
    select
        'orphan_refund', 'warning', 'refund',
        r.refund_id || '-' || r.line_id,
        null,
        {{ iso_week('r.refund_date') }},
        r.refunded_net_revenue_ex_vat + r.refunded_shipping_ex_vat,
        'refund for order ' || r.order_id || ' not matched to a revenue-status order line'
    from {{ ref('int_line_refunds') }} as r
    left join {{ ref('fct_order_lines_profit') }} as f on f.line_id = r.line_id
    where f.line_id is null
),

etl_rejects as (
    select
        'etl_reject', 'error', entity,
        source_id,
        null,
        {{ iso_week(local_date('rejected_at')) }},
        null::numeric,
        reason
    from {{ ref('stg_etl_rejects') }}
)

select * from excluded_orders
union all select * from missing_costs
union all select * from missing_order_meta
union all select * from orphan_refunds
union all select * from etl_rejects
