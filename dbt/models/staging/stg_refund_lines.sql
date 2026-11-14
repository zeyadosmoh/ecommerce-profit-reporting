-- One row per refunded line item. WooCommerce stores refund quantities/amounts as negatives;
-- they are flipped to positives here. The order line is found via meta _refunded_item_id.
select
    r.id                                            as refund_id,
    r.order_id,
    (li ->> 'id')::bigint                           as refund_item_id,
    (
        select (m ->> 'value')::bigint
        from jsonb_array_elements(coalesce(li -> 'meta_data', '[]'::jsonb)) as m
        where m ->> 'key' = '_refunded_item_id'
        limit 1
    )                                               as line_id,
    li ->> 'sku'                                    as sku,
    -(li ->> 'quantity')::int                       as refunded_qty,
    (-(li ->> 'total')::numeric)::numeric(12,2)     as refunded_net_revenue_ex_vat,
    (-(li ->> 'total_tax')::numeric)::numeric(12,2) as refunded_vat
from {{ source('raw', 'raw_refunds') }} as r
cross join lateral jsonb_array_elements(r.line_items) as li
