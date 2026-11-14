select
    id              as product_id,
    sku,
    name            as product_name,
    category,
    status          as product_status,
    price           as price_incl_vat,
    date_modified_gmt as modified_at
from {{ source('raw', 'raw_products') }}
