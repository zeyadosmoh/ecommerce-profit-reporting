-- For every order: sum(line net revenue ex-VAT) + shipping charged ex-VAT == raw total - raw total_tax.
-- Compared against the raw source, not staging, so a staging bug cannot hide itself.
with lines as (
    select order_id, sum(total) as net_revenue_ex_vat
    from {{ source('raw', 'raw_order_lines') }}
    group by 1
)

select o.id as order_id, o.total, o.total_tax, l.net_revenue_ex_vat, f.net_revenue_ex_vat as mart_net_revenue,
       f.shipping_charged_ex_vat as mart_shipping
from {{ source('raw', 'raw_orders') }} as o
join lines as l on l.order_id = o.id
left join {{ ref('fct_orders_profit') }} as f on f.order_id = o.id
where l.net_revenue_ex_vat + o.shipping_total <> o.total - o.total_tax
   or (f.order_id is not null and f.net_revenue_ex_vat + f.shipping_charged_ex_vat <> o.total - o.total_tax)
