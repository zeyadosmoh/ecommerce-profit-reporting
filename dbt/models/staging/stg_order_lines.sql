-- One row per order line. WooCommerce line amounts are already ex-VAT; VAT is kept only for reconciliation.
select
    order_id,
    line_id,
    product_id,
    sku,
    name                    as product_name,
    quantity,
    subtotal                as gross_revenue_ex_vat,       -- before discount
    subtotal - total        as discount_ex_vat,
    total                   as net_revenue_ex_vat,         -- after discount: the revenue basis
    total_tax               as line_vat
from {{ source('raw', 'raw_order_lines') }}
