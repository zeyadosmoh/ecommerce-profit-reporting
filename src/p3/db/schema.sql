-- Raw landing layer. Idempotent: safe to run repeatedly.
-- Money is NUMERIC(12,2) in SAR. Order/line amounts follow WooCommerce: ex-VAT amount + separate *_tax column.

CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.etl_runs (
    run_id         uuid PRIMARY KEY,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    status         text NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    rows_read      integer NOT NULL DEFAULT 0,
    rows_upserted  integer NOT NULL DEFAULT 0,
    rows_rejected  integer NOT NULL DEFAULT 0,
    stats          jsonb NOT NULL DEFAULT '{}'::jsonb,   -- per-entity breakdown + watermarks used
    error          text
);

CREATE TABLE IF NOT EXISTS raw.etl_rejects (
    id           bigserial PRIMARY KEY,
    run_id       uuid NOT NULL REFERENCES raw.etl_runs (run_id),
    entity       text NOT NULL,
    source_id    text,
    reason       text NOT NULL,
    payload      jsonb,
    rejected_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS etl_rejects_run_idx ON raw.etl_rejects (run_id);

-- Incremental watermark per entity; written in the same transaction as the data it describes.
CREATE TABLE IF NOT EXISTS raw.etl_watermarks (
    entity      text PRIMARY KEY,
    watermark   timestamptz NOT NULL,
    run_id      uuid NOT NULL REFERENCES raw.etl_runs (run_id),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw.raw_products (
    id                 bigint PRIMARY KEY,
    sku                text NOT NULL,
    name               text NOT NULL,
    category           text,
    status             text NOT NULL,
    price              numeric(12,2) NOT NULL,   -- VAT-inclusive shelf price (prices_include_tax = yes)
    regular_price      numeric(12,2),
    tax_status         text,
    date_created_gmt   timestamptz,
    date_modified_gmt  timestamptz NOT NULL,
    payload            jsonb NOT NULL,
    payload_hash       text NOT NULL,
    last_run_id        uuid NOT NULL,
    updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw.raw_orders (
    id                  bigint PRIMARY KEY,
    number              text,
    status              text NOT NULL,
    currency            text NOT NULL,
    prices_include_tax  boolean,
    customer_id         bigint,
    payment_method      text,
    billing_city        text,
    date_created_gmt    timestamptz NOT NULL,
    date_modified_gmt   timestamptz NOT NULL,
    date_paid_gmt       timestamptz,
    date_completed_gmt  timestamptz,
    discount_total      numeric(12,2) NOT NULL,   -- ex-VAT
    discount_tax        numeric(12,2) NOT NULL,
    shipping_total      numeric(12,2) NOT NULL,   -- shipping charged to customer, ex-VAT
    shipping_tax        numeric(12,2) NOT NULL,
    total_tax           numeric(12,2) NOT NULL,   -- all VAT on the order
    total               numeric(12,2) NOT NULL,   -- grand total, VAT-inclusive
    coupon_codes        text[] NOT NULL DEFAULT '{}',
    shipping_cost       numeric(12,2),            -- meta_data _shipping_cost: carrier cost to the store, ex-VAT
    payment_fee         numeric(12,2),            -- meta_data _payment_gateway_fee
    payload             jsonb NOT NULL,
    payload_hash        text NOT NULL,
    last_run_id         uuid NOT NULL,
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS raw_orders_created_idx ON raw.raw_orders (date_created_gmt);

CREATE TABLE IF NOT EXISTS raw.raw_order_lines (
    order_id      bigint NOT NULL REFERENCES raw.raw_orders (id) ON DELETE CASCADE,
    line_id       bigint NOT NULL,
    product_id    bigint,
    sku           text,
    name          text,
    quantity      integer NOT NULL,
    price         numeric(12,2),                -- unit price after discount, ex-VAT
    subtotal      numeric(12,2) NOT NULL,       -- before discount, ex-VAT
    subtotal_tax  numeric(12,2) NOT NULL,
    total         numeric(12,2) NOT NULL,       -- after discount, ex-VAT
    total_tax     numeric(12,2) NOT NULL,
    payload       jsonb NOT NULL,
    PRIMARY KEY (order_id, line_id)
);
CREATE INDEX IF NOT EXISTS raw_order_lines_sku_idx ON raw.raw_order_lines (sku);

CREATE TABLE IF NOT EXISTS raw.raw_refunds (
    id                bigint PRIMARY KEY,
    order_id          bigint NOT NULL,          -- no FK: a refund must not be lost if its order was rejected
    date_created_gmt  timestamptz NOT NULL,
    amount            numeric(12,2) NOT NULL,   -- VAT-inclusive amount returned to the customer
    reason            text,
    refunded_payment  boolean,
    line_items        jsonb NOT NULL DEFAULT '[]'::jsonb,
    payload           jsonb NOT NULL,
    payload_hash      text NOT NULL,
    last_run_id       uuid NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS raw_refunds_order_idx ON raw.raw_refunds (order_id);

-- Cost sheet snapshot (one row per sku per effective_from). Costs are ex-VAT.
CREATE TABLE IF NOT EXISTS raw.raw_costs (
    sku             text NOT NULL,
    effective_from  date NOT NULL,
    unit_cost       numeric(12,2) NOT NULL,
    packaging_cost  numeric(12,2) NOT NULL,
    source          text NOT NULL,
    payload         jsonb NOT NULL,
    payload_hash    text NOT NULL,
    last_run_id     uuid NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sku, effective_from)
);
