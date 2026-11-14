-- Cost sheet rows (ex-VAT) with the date range each row is valid for: [effective_from, next_effective_from).
select
    sku,
    effective_from,
    lead(effective_from) over (partition by sku order by effective_from) as next_effective_from,
    unit_cost,
    packaging_cost,
    unit_cost + packaging_cost as unit_landed_cost
from {{ source('raw', 'raw_costs') }}
