-- VAT must never be part of revenue:
--   * every line's net revenue equals the raw ex-VAT line total, and its VAT is ~15% of that (not of an
--     incl-VAT figure);
--   * whole-period net sales in the weekly mart equal the raw ex-VAT line totals of revenue-status orders,
--     and differ from the VAT-inclusive totals by exactly the VAT.
with raw_lines as (
    select l.order_id, l.line_id, l.total, l.total_tax
    from {{ source('raw', 'raw_order_lines') }} as l
    join {{ source('raw', 'raw_orders') }} as o on o.id = l.order_id
    where o.status in {{ revenue_status_list() }}
),

line_check as (
    select f.line_id::text as id, 'line revenue is not the ex-VAT total' as problem
    from {{ ref('fct_order_lines_profit') }} as f
    join raw_lines as r on r.line_id = f.line_id
    where f.net_revenue_ex_vat <> r.total
       or abs(r.total * 0.15 - r.total_tax) > 0.01
),

totals as (
    select sum(total) as ex_vat, sum(total + total_tax) as incl_vat, sum(total_tax) as vat from raw_lines
),

mart as (
    select sum(net_sales_ex_vat) as net_sales from {{ ref('agg_weekly_kpis') }}
)

select * from line_check
union all
select 'period', 'weekly net sales do not equal raw ex-VAT line totals'
from totals cross join mart
where mart.net_sales <> totals.ex_vat or totals.incl_vat - mart.net_sales <> totals.vat
