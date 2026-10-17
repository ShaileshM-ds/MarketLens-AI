# User manual

## 1. Start and choose a mode

Follow the root README. Demo mode requires no account or provider key to explore. The persistent top-right badge identifies synthetic data. Imported and Live modes have separate databases and accounts. Switching mode clears selected products and client identity state; sign in again for that mode.

## 2. Overview and product explorer

Overview shows counts derived from MongoDB, eligible deal candidates and historical coverage. Use Explore products to search, filter category/brand/coverage and sort by name, price or history length. Grid and table layouts use the same API results. Select up to four products and compare their current price, own-history score, volatility, forecast availability and audit count. Different products and currencies must not be treated as interchangeable.

## 3. Product intelligence

Overview and Price history show observed price and reference series. Drag the chart zoom range or use mouse-wheel zoom inside the plot. Toggle series through the legend. Open Accessible chart data for exact minor-unit values. Use 7/30/90/180-day presets; the scoring minimum intentionally prevents a seven-day-only score.

Time Machine accepts a requested date and comparison date in UTC. Read the actual capture time and age, not only the selected date. Previous/next and playback move through observed captures. Changed fields are explicitly labeled. Open source evidence to inspect raw and normalized data.

Seller intelligence shows price series, a correlation table, similarity graph and flagged intervals when identified seller observations suffice. Future Price Memory shows completed historical episodes and outcomes. Forecast & advisor lets you choose horizon, purchase urgency and a target; the page discloses measured error, interval coverage, model baselines and abstentions. Audit evidence links findings to source observations and benign explanations.

## 4. Personal watches and alerts

Open the account button and start a private demo session or register a consumer account with a password of at least 12 characters. Add a watch on a product. Under Watchlist & alerts, edit the target (integer minor units), notes, horizon and rules; Save settings persists changes. Pause retains settings; Remove deletes the watch. Browser reload does not delete the watch or session.

The worker evaluates periodically. An alert arrives when a selected condition crosses from false to true and cooldown permits. Mark read updates its persistent state. The email field is an honest local outbox status, not a delivery claim. Use an admin-controlled demo clock for repeatable historical transitions.

## 5. Analyst dashboard

Read coverage, findings and forecast-versus-baseline results. Filter to products with findings, open their evidence, or export JSON. The export includes synthetic/provenance labeling. Common movement can explain seller similarity; a flag is never a legal conclusion.

## 6. Administration

Create an administrator through `scripts/manage.py`; ordinary registration never grants admin. Imported mode offers file selection/paste, profile, mapping, refreshed preview and commit. Inspect run summaries and quarantined records. Live mode shows configuration status without secrets, bounded collection actions and daily schedules. Paid actions remain disabled until server-side credentials and the enable flag are configured. Demo mode permits a controlled clock and a confirmed reset of its dedicated records only.

## 7. Errors and empty states

No products in an imported/live mode means no usable records have been ingested there. Missing timestamp means historical analysis cannot use the observation. Insufficient history is a result, not an application failure. Database unavailability returns a retryable service error. Sign-in and CSRF errors require a valid session. Provider `needs_review` means an ambiguous paid submission must be reconciled manually.

## 8. Accessibility

The interface supports keyboard focus, form labels, text status indicators, light/dark themes, reduced motion and responsive navigation. Charts have table alternatives. UTC is explicitly displayed. Screenshots in this package are actual browser captures, not design mockups.
