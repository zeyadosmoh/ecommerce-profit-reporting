-- 1. free-shipping part + paid-shipping part = total subsidy, exactly, every week.
-- 2. Free-shipping orders are charged 0 shipping, so their part is exactly their shipping cost.
-- 3. Free-shipping orders respect the store rule: products total incl. VAT >= the threshold var.
select 'parts do not sum' as problem, week_start::text as id
from {{ ref('agg_weekly_shipping') }}
where free_shipping_part_ex_vat + paid_shipping_part_ex_vat <> shipping_subsidy_ex_vat
   or shipping_cost_ex_vat - shipping_charged_ex_vat <> shipping_subsidy_ex_vat
union all
select 'free-shipping order charged for shipping', order_id::text
from {{ ref('stg_orders') }}
where is_revenue_status and is_free_shipping and shipping_charged_ex_vat <> 0
union all
select 'free shipping below threshold', order_id::text
from {{ ref('stg_orders') }}
where is_revenue_status and is_free_shipping
  and products_total_incl_vat < {{ var('free_shipping_threshold_incl_vat') }}
union all
select 'weekly shipping does not tie to agg_weekly_kpis', s.week_start::text
from {{ ref('agg_weekly_shipping') }} as s
join {{ ref('agg_weekly_kpis') }} as k on k.week_start = s.week_start
where s.shipping_charged_ex_vat <> k.shipping_charged_ex_vat or s.shipping_cost_ex_vat <> k.shipping_cost_ex_vat
