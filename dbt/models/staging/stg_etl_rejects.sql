select
    id          as reject_id,
    run_id,
    entity,
    source_id,
    reason,
    rejected_at
from {{ source('raw', 'etl_rejects') }}
