-- One row per refund. Refunded shipping (ex-VAT) comes from the refund's shipping_lines.
select
    r.id                                    as refund_id,
    r.order_id,
    r.date_created_gmt                      as refunded_at,
    {{ local_date('r.date_created_gmt') }}  as refund_date,
    r.amount                                as refund_amount_incl_vat,
    r.reason,
    coalesce((
        select sum(-(s ->> 'total')::numeric)
        from jsonb_array_elements(coalesce(r.payload -> 'shipping_lines', '[]'::jsonb)) as s
    ), 0)::numeric(12,2)                    as refunded_shipping_ex_vat
from {{ source('raw', 'raw_refunds') }} as r
