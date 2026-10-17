# Data dictionary and dataset card

## Dataset card

**Name:** MarketLens deterministic demonstration v1. **Mode:** synthetic only. **Generator:** `backend/app/services/demo.py`, seed 731. **Products:** 36. **Offer observations:** 7,818. **Maximum daily history:** 190 days. **Cutoff:** 2026-10-14T12:00:00Z. **Marketplace label:** amazon.in, used only as a synthetic context. **Currency:** INR. **Sellers:** fictional identities. **Purpose:** reproducible demonstration and software tests. **Not suitable for:** real seller assessments or estimates of market effectiveness.

There are two multi-seller products, one intraday scenario, one eight-day sparse history and one systematically missing-day example. Unknown source capabilities such as captures and countdowns remain unavailable. The original snapshot was not supplied. The bundled import example is schema illustration, not authentic Amazon evidence.

| Collection / field | Type | Meaning and constraints |
|---|---|---|
| products.id | string | Hash of marketplace, external ID, variant and condition |
| products.external_id | string | ASIN or source-specific identifier |
| products.parent_id | nullable string | Parent external product when supplied |
| products.marketplace | string | Marketplace domain; part of identity |
| products.variant | object/string | Preserved variant attributes; no title-based merging |
| products.condition | string | Observed condition or unknown |
| products.title / brand / category | string | Catalog labels; historical versions copied into observations |
| observations.key | unique string | Stable source-record deduplication key |
| observations.product_id | string | Product foreign key |
| observations.seller_id | nullable string | Identified seller; unknown is not invented |
| observations.price | positive integer | Item price in currency minor units |
| observations.shipping | nullable integer | Known shipping; null is not zero |
| observations.reference_price | nullable integer | Observed reference/list price |
| observations.reference_type | string | Source meaning or unknown; not universally called MRP |
| observations.tax_treatment | string | Included or unknown/source value |
| observations.total | nullable integer | Item + shipping only when shipping known and tax included |
| observations.coupon | nullable integer | Observed conditional reduction |
| observations.coupon_conditions | nullable string | Eligibility evidence |
| observations.conditional_coupon_price | nullable integer | Item minus coupon when conditions supplied; excluded from ordinary baseline |
| observations.currency | enum string | INR, USD, GBP, EUR, JPY, CAD, AUD |
| observations.region | nullable string | Collection context; comparisons require equality |
| observations.buy_box | boolean | Explicit observed offer scope |
| observations.availability | nullable string | Observed availability, not inferred quantity |
| observations.urgency / countdown | nullable | Source marketing evidence only |
| observations.delivery / rating / review_count | nullable | Source values retained |
| observations.metadata | object | Product metadata captured with this observation |
| observations.observed_at | nullable UTC ISO string | Source observation time; timezone required |
| observations.ingested_at | UTC ISO string | Time available to the system; demo uses controlled synthetic time |
| observations.source_type / source_id | string | Mode and source identifier |
| observations.run_id | string | Ingestion run identifier |
| observations.synthetic | boolean | True only in demo database |
| observations.timestamp_quality | enum | Explicit or missing |
| observations.schema_version | string | Normalization schema version |
| observations.raw_ref | string | Hash reference to preserved original input |
| observations.quality_flags | array | Missing timestamp / incomplete total and future extensions |
| raw.record | object/value | Original source record, rendered only as text |
| runs | document | Accepted, duplicate, rejected, warning counts and completion |
| quarantine | document | Run, raw reference and rejection reason |
| jobs | document | Kind, queued/polling/working/submitting/retry/done/failed/needs_review, due, attempts, owner, lease, provider snapshot |
| analyses.result | object | Quality, discount, sellers, memory, forecast, audit and advisor with cutoff/version |
| users | document | ID, username, scrypt password hash when registered, role |
| sessions | document | Hashed random token, CSRF value, owner, TTL expiry |
| watchlists | document | Owner/product unique pair, notes, target, horizon, window, pause/rules and transition state |
| notifications | document | Owner/event unique pair, evidence, synthetic label, read and delivery state |
| schedules | document | URLs, bounded interval, enabled, due and last scheduling error |
| usage / limits | document | Conservative daily collection reservation and expiring request counters |

All mode-relevant API responses include metadata with mode, synthetic flag, timestamp, schema and analysis versions. JSON exports preserve the mode designation and do not create executable spreadsheet cells.
