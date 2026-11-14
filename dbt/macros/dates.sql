{# Store-local calendar date of a timestamptz column. #}
{% macro local_date(col) -%}
    (({{ col }}) at time zone '{{ var("store_timezone") }}')::date
{%- endmacro %}

{# ISO week start (Monday) of a date expression. #}
{% macro iso_week(date_expr) -%}
    date_trunc('week', ({{ date_expr }})::timestamp)::date
{%- endmacro %}

{# SQL list literal of the statuses that count as revenue. #}
{% macro revenue_status_list() -%}
    ({% for s in var("revenue_statuses") %}'{{ s }}'{% if not loop.last %}, {% endif %}{% endfor %})
{%- endmacro %}
