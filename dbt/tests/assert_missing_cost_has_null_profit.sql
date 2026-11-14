-- Missing cost must never turn into zero cost: no missing-cost row may carry a COGS or profit value,
-- in the line fact, the ledger, or the order fact (orders with any missing line have NULL profit).
select 'fct_order_lines_profit' as model, line_id::text as id
from {{ ref('fct_order_lines_profit') }}
where cost_missing and (cogs is not null or cogs_reversed is not null or net_cogs is not null
                        or gross_profit is not null or net_profit is not null)
union all
select 'int_profit_ledger', line_id::text
from {{ ref('int_profit_ledger') }}
where cost_missing and (cogs is not null or gross_profit is not null or net_profit is not null)
union all
select 'fct_orders_profit', order_id::text
from {{ ref('fct_orders_profit') }}
where has_missing_cost and (net_cogs is not null or gross_profit is not null or net_profit is not null)
union all
select 'fct_order_lines_profit: costed line with NULL profit', line_id::text
from {{ ref('fct_order_lines_profit') }}
where not cost_missing and (cogs is null or net_profit is null)
