{#
  Allocate a group-level amount to the rows of the group in proportion to `weight`, exactly to the halala.

  Largest-remainder method, in integer halalas:
    share_i  = amount * weight_i / sum(weight)          (equal shares if sum(weight) = 0)
    alloc_i  = floor(share_i)
    leftover = amount - sum(alloc_i)                     (0 <= leftover < row count)
    the `leftover` rows with the largest fractional part (ties: lowest key) get +1 halala.
  So sum(alloc_i) == amount for every group, always.

  `relation` is a CTE/relation with one row per member, carrying the group-level amount on every row.
  Returns a parenthesised subquery with columns (<group_key>, <member_key>, <alias>).
#}
{% macro allocate_largest_remainder(relation, group_key, member_key, weight, amount, alias) %}
(
    with _base as (
        select
            {{ group_key }}                              as _g,
            {{ member_key }}                             as _k,
            greatest(coalesce({{ weight }}, 0), 0)::numeric as _w,
            round(coalesce({{ amount }}, 0) * 100)::bigint as _amount_c
        from {{ relation }}
    ),
    _shares as (
        select
            *,
            case
                when sum(_w) over (partition by _g) > 0
                    then _amount_c * _w / sum(_w) over (partition by _g)
                else _amount_c::numeric / count(*) over (partition by _g)
            end as _share
        from _base
    ),
    _floored as (
        select *, floor(_share)::bigint as _floor, _share - floor(_share) as _frac
        from _shares
    ),
    _ranked as (
        select
            *,
            _amount_c - sum(_floor) over (partition by _g)                    as _leftover,
            row_number() over (partition by _g order by _frac desc, _k asc)   as _rank
        from _floored
    )
    select
        _g as {{ group_key }},
        _k as {{ member_key }},
        ((_floor + case when _rank <= _leftover then 1 else 0 end) / 100.0)::numeric(12,2) as {{ alias }}
    from _ranked
)
{% endmacro %}
