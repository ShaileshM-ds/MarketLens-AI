# Analysis and evaluation cards

## Discount heuristic v1

**Purpose:** Explain a current offer relative to its comparable historical prices. **Inputs:** known landed or explicitly incomplete item prices; daily medians; source reference price when observed. **Output:** score 0–100, savings, percentile, baseline statistics, coverage, evidence keys. **Minimum:** 14 baseline days and 60% selected-window coverage. **Weights:** savings 0.60, price position 0.25, claim agreement 0.15, renormalized when claim unavailable. **Risks:** unrepresentative collection context, incomplete shipping/tax, reference meaning differences. **Prohibited interpretation:** probability of seller honesty or fraud detection.

## Seller-pattern heuristic v1

**Purpose:** Review unusual co-movement in identified seller offers. **Inputs:** consecutive observed daily price returns, at least 20 overlaps and five material changes. **Outputs:** correlation, same-direction frequency, magnitude similarity, half-history stability, limited daily lag correlations, common-movement flag, shuffled-null p-value, screened-pair adjustment, score and confidence. **Decision:** score >=75, adjusted p <=0.05 and correlation above 0.5 in both halves. **Known limitations:** no exact intraday ordering, causal inference, comprehensive category/demand/promotion controls or real-world calibration. **Evaluation:** seeded positive and broad-movement negative scenarios; exact counts in evaluation.json.

## Historical event memory v1

**Purpose:** Retrieve completed same-product event analogs. **Inputs:** recent price returns, deviation, volatility, regime duration, source-supported field transitions and weekday. **Events:** price rise/drop, reference rise, return to recent high, seller switch, stockout/restock, shipping rise, repeated promotion. **Leakage controls:** reliable cutoff, full 14-day observed outcome window, at least 14 days between selected episodes, preprocessing fitted only on eligible candidate history. **Outputs:** distances, event types, 1/3/7/14-day outcomes, empirical counts, spread and time to lower. **Limitations:** small samples, heuristic event thresholds, no calibrated probability, no category-level analog expansion.

## Forecast v1

**Purpose:** Estimate 7/14/30-day prices where observed targets support evaluation. **Candidates:** persistence, seven-observation moving average, exponential smoothing alpha 0.3, and seasonal naive for long complete daily data. **Selection:** early rolling origins; require 2% improvement over persistence. **Testing:** later origins; no random split. **Metrics:** MAE, MAE divided by mean absolute one-step change (null when zero scale), sample counts and date ranges. **Interval:** 90th percentile absolute calibration residual, reused across path; held-out horizon-end coverage measured separately. **Limitations:** no daily-specific calibration, limited samples, regime shifts and missing-day effects. **Failure example:** seeded genuine discount yields about 66.7% held-out coverage, not 90%.

## Advisor policy v1

**Purpose:** Assist a shopper with a transparent policy. **Inputs:** current historical percentile/savings, freshness, forecast bound, availability, urgency, target and historical analog context. **Outputs:** Buy now, Consider waiting, Insufficient evidence; reasons and limitations. **Abstention:** insufficient/stale history, mixed signals, or observed out-of-stock offer. **Risks:** preference mismatch and changes after the last capture. **Prohibited interpretation:** guaranteed future savings.

## Evaluation protocol

`python scripts/evaluate.py` regenerates the synthetic measurement file directly from generator and shared analysis services. Tests separately verify missing-history states, constant sellers, no future leakage, incompatible currency exclusion, chronological baselines, interval coverage, positive/negative seller scenarios and alert transitions. No external dataset effectiveness or paid provider verification is asserted.
