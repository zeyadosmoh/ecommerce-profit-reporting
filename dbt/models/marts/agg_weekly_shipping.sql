-- Shipping subsidy per ISO order week (revenue-status orders), ex-VAT, at sale (before refunds).
--   shipping_subsidy = shipping cost - shipping charged
--                    = free_shipping_part (cost of free-shipping orders; they are charged 0)
--                    + paid_shipping_part (cost - charged on paid-shipping orders; may be negative)
-- The two parts sum to the total exactly; there is no ratio that could exceed 100%.
select
    order_week                                                                     as week_start,
    order_week + 6                                                                 as week_end,
    count(*)                                                                       as orders,
    count(*) filter (where is_free_shipping)                                       as free_shipping_orders,
    count(*) filter (where not is_free_shipping)                                   as paid_shipping_orders,
    sum(shipping_charged_ex_vat)                                                   as shipping_charged_ex_vat,
    sum(coalesce(shipping_cost_ex_vat, 0))                                         as shipping_cost_ex_vat,
    sum(coalesce(shipping_cost_ex_vat, 0) - shipping_charged_ex_vat)               as shipping_subsidy_ex_vat,
    coalesce(sum(coalesce(shipping_cost_ex_vat, 0) - shipping_charged_ex_vat)
             filter (where is_free_shipping), 0)                                   as free_shipping_part_ex_vat,
    coalesce(sum(coalesce(shipping_cost_ex_vat, 0) - shipping_charged_ex_vat)
             filter (where not is_free_shipping), 0)                               as paid_shipping_part_ex_vat
from {{ ref('stg_orders') }}
where is_revenue_status
group by 1, 2
