-- One row per order. Amounts are ex-VAT unless the column name says otherwise.
select
    id                                  as order_id,
    status                              as order_status,
    status in {{ revenue_status_list() }} as is_revenue_status,
    currency,
    customer_id,
    payment_method,
    billing_city,
    date_created_gmt                    as created_at,
    {{ local_date('date_created_gmt') }} as order_date,
    {{ iso_week(local_date('date_created_gmt')) }} as order_week,
    date_modified_gmt                   as modified_at,
    discount_total                      as discount_ex_vat,
    shipping_total                      as shipping_charged_ex_vat,
    exists (
        select 1 from jsonb_array_elements(coalesce(payload -> 'shipping_lines', '[]'::jsonb)) as s
        where s ->> 'method_id' = 'free_shipping'
    )                                   as is_free_shipping,
    shipping_cost                       as shipping_cost_ex_vat,
    payment_fee,
    total - total_tax                   as total_ex_vat,
    total - shipping_total - shipping_tax as products_total_incl_vat,
    total_tax                           as vat_total,
    total                               as total_incl_vat,
    coupon_codes
from {{ source('raw', 'raw_orders') }}
