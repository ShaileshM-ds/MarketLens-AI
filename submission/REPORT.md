# MarketLens AI
## Explainable Marketplace Intelligence and Historical Price Auditing

**Student(s):** [Student names]  
**Institution:** [Institution]  
**Department / programme:** [Department and programme]  
**Supervisor:** [Supervisor]  
**Academic year:** [Academic year]  
**Submission status:** Implemented local prototype with measured synthetic evaluation. External provider and public deployment remain unverified.

## 1. Abstract

MarketLens AI integrates historical marketplace observations, explainable discount scoring, historical-state reconstruction, seller-pattern analysis, event analog retrieval, forecasting and personal alerts. The system preserves immutable observations in MongoDB, distinguishes synthetic demonstration records from imported and live data, and connects a React interface to a FastAPI service and separate worker. Its analytical policies return insufficient-evidence states when history, timing or comparability is inadequate. A deterministic demonstration contains 36 fictional products and 7,818 offer observations. Verification includes real-database integration tests and browser journeys; measured scenario results are supplied as reproducible JSON. The contribution is an integrated, inspectable decision-support workflow, not a claim of novel forecasting theory or automated proof of misleading conduct.

## 2. Problem statement

An advertised discount compares a current price with a displayed reference price. That does not directly answer whether the current offer is favorable relative to observed market history. Historical sellers, shipping costs, availability and promotion messages may also matter, yet a present-day snapshot cannot reconstruct them reliably. A useful student-scale system must preserve the evidence, explain each comparison, distinguish estimates from observations, and abstain when data is insufficient.

## 3. Objectives and scope

The project aims to preserve provenance-linked marketplace observations; compare prices within explicit currency, condition, variant and region contexts; reconstruct the latest known state at a historical cutoff; identify repeated price and marketing patterns; retrieve completed historical analogs; evaluate lightweight forecasts chronologically; and provide personal watchlists and in-app alerts. Administration supports content-aware imports, raw retention, quarantine, provider jobs and controlled demo reset.

The implemented scope is a local full-stack prototype and container deployment package. It does not establish real seller intent, false stock quantities, legal wrongdoing, calibrated purchase probabilities, or guaranteed savings. No original Amazon dataset or live scraping credential was supplied.

## 4. Motivation and research questions

1. How does a daily historical median differ from an observed reference-price discount?
2. Can an as-of reconstruction preserve historical metadata without selecting future records?
3. Which repeated seller changes remain unusual after considering other observed sellers?
4. What completed price outcomes followed similar historical events?
5. Does a lightweight forecast improve on a last-price baseline on later chronological origins?
6. Can a consumer trace a recommendation or alert back to stored evidence?

These are investigable engineering questions. This project does not report a user survey, controlled shopper trial or real-world causal experiment.

## 5. Existing systems and contribution

Keepa's official site describes price-history charts and price-drop alerts. camelcamelcamel's official feature page describes threshold-based email alerts, price histories and product search. These establish that historical price tracking is an existing capability, so the project does not claim to invent it. This comparison is limited to features explicitly stated by those sources; absence from their short descriptions does not prove a competitor lacks a capability.

MarketLens combines inspectable raw observations, explicit data-mode isolation, historical-state cards, daily-median discount explanations, seller change evidence, completed-event analogs and chronological forecast evaluation in a single academic prototype. Its distinctive emphasis is explainability and reproducibility across the workflow.

References: [Keepa](https://keepa.com/); [camelcamelcamel features](https://izer.camelcamelcamel.com/features). Accessed 2026-10-14/15.

## 6. Architecture

The React/TypeScript frontend uses TanStack Query for server state and ECharts for interactive charts with exact-value table alternatives. FastAPI supplies versioned JSON APIs and OpenAPI. MongoDB stores catalog records, immutable observations, raw input, ingestion runs, jobs, analyses, users, sessions, watches and notifications. A separate Python worker claims jobs with leases, polls collection status, recalculates affected product analyses and evaluates alert transitions. Local, synthetic and Bright Data sources share normalization and analytical services.

The three databases are named with a configured prefix and `_demo`, `_imported`, `_live` suffixes. Mode selection is validated by the API and selects a database; it is not a filter that can accidentally merge observations. Users and sessions are also mode-isolated.

Editable diagrams are in `DIAGRAMS.md`: architecture, use cases, data flow, entity relationships and ingestion/analysis sequences.

## 7. Data model and provenance

Product identity hashes marketplace, external identifier, variant attributes and condition. Similar titles do not merge distinct products. Money is stored in integer minor units, with JPY using a zero-decimal scale and supported other currencies using two decimals. Binary floating-point is used only for derived statistics, not stored input prices.

An observation includes product and seller identity; item and reference prices; reference-price type; shipping and tax knowledge; optional coupon and eligibility text; comparable total when known; currency; region; Buy Box; availability; urgency and countdown evidence; delivery; ratings; review count; historical product metadata; raw source reference; source type and identifier; run identifier; observation and ingestion times; synthetic flag; timestamp quality; schema version; and quality flags.

Unique observation keys remove repeated imports while separate timestamps or source record changes remain distinct. Raw records are stored separately. Invalid records enter quarantine with an understandable reason. The full dictionary and dataset card are in `DATA_DICTIONARY.md`.

## 8. Dataset inspection and import methodology

The original Amazon snapshot was absent. The workspace's `sources/` directory was empty, and no synced reference file was changed. Consequently this report does not fabricate its format, number of sellers, timestamp span or quality.

The importer detects JSON arrays, JSONL and CSV from contents. Preview reports field coverage and a normalized sample before writing observations. A field-mapping interface supports canonical identifiers, monetary values, timing and offers. Malformed JSONL lines can be quarantined; structurally invalid array/CSV input returns a parse error. A missing observation timestamp remains null; ingestion time is never substituted as historical time.

A single snapshot can initialize catalog entries. It cannot establish price history simply because it includes many products. Seller-count fields alone cannot support synchronization analysis. Additional-seller arrays are retained raw and require explicit row flattening/mapping before use.

## 9. Comparable prices and quality

Analysis starts from observations with explicit times at or before the cutoff and with ingestion times no later than that cutoff. Within a product, the selected scope uses observed Buy Box offers when available, otherwise the latest seller's offers. Currency, region, condition, variant and total-completeness context must match. Known landed price includes item and known shipping when tax treatment is included. Otherwise the analysis is explicitly item-price-based and the total is incomplete. Conditional coupons are displayed separately and excluded from the ordinary baseline.

Quality outputs include observation count, distinct observed days, elapsed span, missing days, coverage, freshness, identified sellers, missing timestamp count and feature eligibility. UTC is used for storage and the initial display implementation. A selected time zone other than UTC is a future extension.

## 10. Real Price Detector

For window W, the baseline is the median of daily comparable prices during the preceding W days, excluding the current day. This avoids both self-inclusion and domination by heavily sampled days.

`historical_savings_pct = 100 * (baseline - current) / baseline`

The service preserves negative savings. Initial eligibility requires at least 14 prior distinct days and 60% coverage of the selected window. A seven-day window therefore intentionally cannot produce a score under the minimum-day policy. It can still show observations.

The Genuine Discount Score is a bounded, versioned heuristic: 60% scaled positive historical savings (30% savings reaches the maximum component), 25% favorable historical price percentile, and 15% agreement between observed reference discount and historical savings. Agreement loses four score points per percentage-point gap. Missing reference claims remove that component and renormalize the others. The score is not a probability of honesty; coverage is shown separately.

## 11. Marketplace Time Machine

For a requested cutoff, the service returns the latest eligible observation at or before that time. It shows requested and actual capture times and age. Two-date comparison highlights changes in price, reference, shipping, seller, availability, urgency, delivery, rating, reviews and promotion. Product metadata comes from each observation. Previous/next controls and playback operate on observed captures. The evidence drawer exposes normalized and raw records.

The feature is an observed-state reconstruction. No historical page screenshot or HTML archive is claimed. Captures can be added through the provider extension contract after safe sanitization is designed and verified.

## 12. Seller synchronization analysis

Daily seller offers are aligned only over consecutive days actually observed for both sellers. There is no stale carry-forward or interpolated ground truth. The service requires at least 20 overlapping intervals and five materially changing intervals; constant series are excluded. It calculates price-return correlation, same-direction frequency, an explainable score and a deterministic shuffled null comparison. Bonferroni-style multiplication adjusts p-values across the screened pairs.

Other observed sellers supply a limited common-movement comparison; strong shared movement reduces the anomaly score. This is not comprehensive demand, seasonality or promotion control. The current implementation analyzes daily cadence, even where intraday demo observations exist. It reports minus-one/zero/plus-one-day lag correlations only for contiguous series, checks correlation stability in both halves, and does not claim exact intraday ordering. Heatmap cells, graph links and change-interval tables show the evidence. Wording consistently describes anomalies for investigation, not proof of coordination.

## 13. Future Price Memory

Event detectors select material price changes, reference increases, returns to recent highs, seller switches, stockout/restock transitions, shipping increases and repeated promotions where observed. Features use recent returns, baseline deviation, volatility, regime duration, field changes and weekday available at the event. Candidates are separated by at least 14 days. Every selected candidate has a complete observed 14-day outcome window before the query cutoff. Standardization is fitted only on eligible historical candidate features, followed by interpretable Euclidean nearest-neighbor retrieval within the same product.

The UI reports match count, count with a lower three-day outcome, median change, spread, time to a lower price and each episode's 1/3/7/14-day outcomes. Too few candidates produces an abstention. The empirical fraction is not a calibrated future probability. Missing event fields remain unavailable; no source evidence is invented.

## 14. Forecasting and advisor

Forecasting compares last observed price, seven-observation moving average, exponential smoothing, and a seasonal-naive candidate for sufficiently long complete daily series. It supports 7/14/30-day horizons when length, coverage and target counts permit. Rolling origins are chronological; missing target dates are skipped. Early origins select a model and calibrate a residual interval; later origins measure MAE, scale-relative MAE and coverage. If a candidate does not improve early error by at least 2%, last-price wins.

The uncertainty width is the 90th percentile of absolute early-origin horizon-end residuals. It is reused across the plotted path, so daily bands are not individually calibrated. Measured coverage can be below 90% and is reported honestly. No performance threshold was assumed in advance.

The advisor is an explicit policy combining price position, freshness, forecast bounds, urgency and target price, with historical analog counts in the explanation. It returns Buy now, Consider waiting or Insufficient evidence. It abstains on sparse, stale or mixed signals. This is a shopping aid, not a guarantee.

## 15. Alerts and audit rules

Watchlists persist target, notes, horizon, window, pause state and selected rules. The separate worker evaluates target crossings, new lows, meaningful historical savings, recommendation changes, seller switches, restocks, audit events and synchronization flags. A condition must cross false to true before retriggering. A 24-hour cooldown and unique event key prevent uncontrolled duplicates. Notifications have read state, source evidence and explicit local email-outbox status. No email is claimed sent.

Audit rules detect reference-price rises preceding claims, sale-above-baseline observations, repeated urgency, observed countdown increases, shipping offsets and seller/cost switches. Findings carry version, severity, evidence confidence, source keys, timestamps and benign explanations. Unsupported evidence fields disable their rules. Repeated scarcity text does not establish actual inventory or false scarcity.

## 16. Security and operations

Passwords use scrypt with random salts. Session tokens are random and stored as hashes; cookies are HttpOnly and SameSite Strict, with Secure enabled in production. Mutations require a session CSRF token. User-owned watches and notifications are filtered by owner. Administrative routes check roles. Login and ingestion have database-backed rate limits, origin checks and upload limits. Provider URLs use an exact HTTPS marketplace allowlist and fixed API host. Imported text is rendered as text, and exports use JSON to avoid spreadsheet formula interpretation.

Durable jobs use atomic claiming, leases, bounded retries and recovery. A worker crash during paid submission leaves an ambiguous state for manual review instead of automatic resubmission. Daily caps are reserved atomically; minimum refresh is checked before queueing. The current student-scale scheduling implementation should be load-tested before multiple concurrent collection submitters are deployed.

Docker Compose supplies frontend, API, worker and private MongoDB with a persistent volume. Health and readiness are provided. HTTPS, production origins, private database access, backups and optional Atlas configuration are documented in `docs/OPERATIONS.md`. No cloud deployment URL is invented.

## 17. Testing and measured results

Machine-readable evidence is in `test-results.xml`, `browser-results.json`, and `evaluation.json`. The evaluation uses deterministic synthetic records only. Verification passed 35 backend tests, three frontend unit tests, and five browser journeys. Full results and environment boundaries are recorded in TESTING_REPORT.md.

Measured scenario outcomes include a score of 86 and approximately 27.67% historical savings for the genuine-discount example; a reference-rise finding for the inflated-reference example; repeated-urgency, shipping-offset and seller-switch findings for their respective examples; one flagged seller pair in the synchronization scenario; and no flagged pairs in the broad common-movement scenario. The sparse product abstains. Stable-price examples select the last-price baseline with zero error on their synthetic held-out targets. These outcomes demonstrate internal behavior, not real marketplace accuracy.

The genuine-discount forecast's seven-day held-out interval coverage is approximately 66.7% across 12 test origins, showing that a nominal residual band need not achieve its intended coverage under a regime change. The report retains this failure case. Exact per-product baselines, MAE, sample counts and ranges remain in the evaluation JSON.

## 18. Limitations and ethics

The real snapshot is missing; the Bright Data integration is fixture-tested, not live-verified. No paid scraping, email delivery or public deployment was performed. Daily seller analysis does not establish intraday ordering. Event memory uses only source-supported event families. Forecast intervals need larger external evaluation. The UI uses UTC and lightweight chart interactions; it does not implement all possible linked chart annotations. Additional-seller arrays require mapping before analysis. These are explicit limits, not concealed simulated successes.

Avoid targeting real sellers with synthetic suspicious labels. Obtain lawful data access and follow provider/marketplace terms. Keep personal user data minimal and protect account credentials. Present findings as evidence for review and preserve alternative explanations. Synthetic results are educational demonstrations only.

## 19. Future scope

Validate real longitudinal datasets; broaden event detectors; add cadence-aware lead/lag stability checks and category controls; calibrate intervals by horizon and data regime; add safe historical captures; improve preprocessing for source schema variants; support optional verified email delivery; and evaluate usability with consenting participants. Public operation also needs resource quotas, monitoring and backup exercises.

## 20. Installation, user manual and handover

See the root `README.md` for Docker and development commands, import procedure, account setup and Bright Data configuration. `USER_MANUAL.md` covers every main screen. `DEMO_AND_VIVA.md` contains five-minute and longer technical demonstrations, viva answers and developer handover notes. `DIAGRAMS.md` and `DATA_DICTIONARY.md` remain editable. Actual screenshots are in `screenshots/`.

## References

1. Keepa. Amazon Price Tracker. https://keepa.com/ (accessed 2026-10-14/15).
2. camelcamelcamel. Features. https://izer.camelcamelcamel.com/features (accessed 2026-10-14/15).
3. Bright Data. Monitor Progress API. https://docs.brightdata.com/api-reference/web-scraper-api/management-apis/monitor-progress (accessed 2026-10-14).
4. Bright Data. Web Scraper API reference examples. https://github.com/brightdata/skills/blob/main/skills/bright-data-best-practices/references/web-scraper-api.md (accessed 2026-10-14).

No survey statistics, real-world accuracy claims or novelty claims beyond integration are asserted.


