# Demonstration scripts and developer handover

## Five-minute demonstration

**0:00–0:30:** Open Overview. Point to the synthetic badge, 36 products and 7,818 observations. Explain the absent original snapshot and that demo sellers are fictional.

**0:30–1:15:** Open Auralis Studio Headphones. Show current price, daily-median baseline, approximately 27.67% historical savings and score 86. Open the component explanation. Clarify that a score is not a probability.

**1:15–2:00:** Open Time Machine. Compare 2026-09-30 with 2026-10-14. Read actual observation times, changed fields and raw evidence. Explain why later captures cannot answer an earlier query.

**2:00–2:45:** Open Forma Desk Lamp from the explorer. Seller intelligence identifies one unusual pair in the synthetic scenario. Contrast Echo Portable Speaker, where broad seller movement is discounted. Do not claim collusion.

**2:45–3:30:** Open Atlas Coffee Maker. Future Price Memory shows completed episodes and 1/3/7/14-day outcomes. Show Forecast & advisor and the measured interval coverage. Explain a historical frequency versus a forecast.

**3:30–4:15:** Start a private demo session. Set a deliberately high target on a product to demonstrate an immediate qualifying condition, then visit Watchlist & alerts. Wait for the worker cycle and show the in-app alert. State that no email was sent.

**4:15–5:00:** Open the sparse Cobalt USB-C Hub. Show abstention. Finish with Analyst dashboard and a JSON evidence export. Mention the importer, local Docker command and unverified external integration.

## Longer technical demonstration (15–20 minutes)

1. Inspect the repository architecture and API OpenAPI page.
2. Explain database isolation and the identity hash; contrast a 128GB and 256GB variant.
3. In Imported mode as admin, load `data/samples/import-example.json`, preview fields and warnings, import twice, and demonstrate duplicate counts.
4. Explain raw retention and missing observation-time exclusion. Show that one snapshot does not create history.
5. Demonstrate current-day exclusion and equal weighting of daily medians.
6. Compare historical captures and raw source keys.
7. Show seller constant-series abstention, shuffled null adjustment and common-movement limitation.
8. Explain event-window completion and non-overlapping analog episodes.
9. Show chronological backtest ranges, sample counts and failure to achieve nominal interval coverage in a changing-price scenario.
10. Create a watch, pause and resume it, change the demo clock and inspect event deduplication.
11. Show the worker process, atomic claim test and ambiguous-submission recovery test.
12. Show disabled live collection and account-specific provider configuration requirements.
13. Run the backend tests and display measured reports, then show mobile layout and theme.

## Viva questions and answers

**Why use the daily median?** It is robust to isolated extremes and prevents a heavily sampled day from dominating the baseline. It still depends on representative observations and does not recover unobserved prices.

**Why exclude the current day?** The offer being assessed should not influence its own historical baseline. Excluding the day also avoids repeated same-day samples leaking into that comparison.

**Is the score a probability that the seller is honest?** No. It is a documented weighted heuristic with separately shown coverage and raw quantities.

**Why MongoDB?** Observations and raw provider outputs have variable optional fields. Ordinary collections permit unique deduplication keys and compound time indexes. The choice is manageable for a student project; it is not a claim that relational storage would be unsuitable.

**Why not manufacture daily data from a snapshot?** That would turn a single observation into invented evidence and invalidate temporal analysis.

**What is the difference between observed and ingested time?** Observed time describes the source capture; ingested time describes when the system obtained it. Historical availability checks need both. Missing observed time is not replaced with ingestion time.

**How is leakage prevented?** Cutoff filtering excludes later observation/ingestion times. Discount baselines exclude the current day. Analog outcomes must finish before the query. Forecast selection and testing use chronological origins.

**How are different currencies handled?** They are not converted or silently mixed. Product identity and context filtering preserve marketplace, variant, condition, region and currency.

**Why can a seven-day view have no score?** The policy requires at least 14 baseline days, so the seven-day selection is deliberately insufficient for scoring.

**Does correlation prove collusion?** No. Shared demand, costs, promotions and marketplace mechanisms can create similar changes. The system shows a limited anomaly signal with alternative explanations.

**Why are constant series excluded?** No-change overlap supplies no evidence of synchronized price decisions and can make naive similarity metrics misleading.

**What does the shuffled null do?** It compares observed correlation with correlations after permuting one seller's changes. Pair-count adjustment reduces multiple-comparison false positives, but does not prove causality or eliminate dependence-related limitations.

**How does Future Price Memory differ from forecasting?** It retrieves historical episodes and reports their observed later outcomes. The forecast fits or selects a predictive rule and evaluates its future-target errors.

**Why might a baseline beat exponential smoothing?** Stable or abrupt regimes can favor persistence; complexity is not automatically useful. The implementation falls back unless early chronological evaluation improves enough.

**What does interval coverage mean?** It is the fraction of held-out horizon-end targets within the interval. A nominal residual quantile need not achieve nominal coverage under distribution changes.

**How do alerts avoid spam?** Condition transitions, a cooldown, and an owner/watch/rule/observation event key with a unique index prevent repeated delivery from unchanged evidence.

**What if the worker crashes during submission?** The job is ambiguous. It requires manual provider reconciliation rather than automatic paid resubmission.

**How are credentials protected?** Environment secrets stay server-side; passwords are salted scrypt hashes; session tokens are stored hashed; cookies are HttpOnly and Secure in production.

**What was verified live?** The local FastAPI/React/MongoDB/worker application and browser flows. Bright Data behavior was tested with fixtures only. No external scraping or public deployment was executed.

**What is the contribution?** Integrating evidence preservation, explainable historical comparisons, event retrieval, seller-pattern review and buyer assistance in one working and testable system.

## Developer handover

Start with `README.md`, then `docs/PROGRESS.md`. Monetary normalization belongs in `services/normalize.py`; analytical policy belongs in `analytics/engine.py`, not UI or route handlers. Any schema change must update normalization, tests and schema version. Any scoring-policy change must update its version and methodology. Keep `sources/` read-only.

Use mode-specific isolated test databases. Do not weaken cutoff or currency checks to make sparse data look better. Keep test fixture outcomes separate from live integration verification. Before changing a provider, confirm its account-specific input/output contract and extend fixtures. Before public deployment, configure HTTPS, production cookies, exact origins, database restrictions and backup monitoring.
