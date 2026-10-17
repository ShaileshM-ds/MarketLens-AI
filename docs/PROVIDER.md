# Bright Data adapter contract

Checked against official documentation during implementation (2026-10-14/15):

- [Monitor progress](https://docs.brightdata.com/api-reference/web-scraper-api/management-apis/monitor-progress): GET `/datasets/v3/progress/{snapshot_id}`; starting, running, ready, failed states.
- [Bright Data official scraper examples](https://github.com/brightdata/skills/blob/main/skills/bright-data-best-practices/references/web-scraper-api.md): asynchronous trigger, snapshot polling and JSON download routes. Examples differ in body wrapping across workflows; validate the configured scraper's input in the provider console.
- [Bright Data collection tutorial](https://brightdata.com/blog/ai/using-tensorflow-analyze-bright-data): URL-array trigger examples. This article concerns a reviews scraper; it establishes a generic request pattern, not a verification of this project's Amazon Products configuration.

Implementation uses POST `/datasets/v3/trigger?dataset_id=...&include_errors=true` with `[{'url': ...}]`, GET progress, then GET `/datasets/v3/snapshot/{snapshot_id}?format=json`. The account's dataset identifier must be supplied. No hardcoded scraper identifier or credentials are shipped.

The provider adapter maps `final_price` objects or numeric values, currency, ASIN, URL-derived marketplace, initial reference price and explicit timestamps. Raw provider records are retained without overwriting them with normalized fields. Missing seller, shipping, tax, urgency, countdown, timestamp or captures remains unknown. Additional-seller arrays are retained for inspection; flatten and explicitly map individual offers for seller analysis. Complete checkout or page-capture evidence is not promised by this adapter.

## Failure handling

401/403 and malformed inputs fail without repeated submission. 429 and download/status network failures use bounded retry delays. A timeout, network failure or 5xx during submission can mean a paid job was accepted: the job enters `needs_review`. An expired lease in `submitting` does the same. Inspect the provider console, then reconcile the snapshot ID through an operator database update; do not blindly submit again. A successful snapshot ID is persisted before polling. Repeated downloads are deduplicated by source observation keys.

Polling and download are not held open by browser requests. The worker polls persistent jobs, reserves daily capacity before submission, and schedules daily collections. The daily reservation is conservative and is not refunded after failed attempts. Cost estimates are omitted unless a real configured rate is provided; they never replace provider billing.

## Verification boundary

Contract fixtures cover successful trigger/status/download, 401, 429, 503, missing fields, and ambiguous submission state. They are representative synthetic/redacted-style fixtures. No API credential was supplied, so no live provider contract or paid collection has been verified. Confirm the exact URL-array body and response mapping in your account before enabling live collection.
