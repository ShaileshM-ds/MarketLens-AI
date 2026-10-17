# Testing report

Verified 2026-10-15 (Asia/Calcutta). All effectiveness evaluation is synthetic; no authentic marketplace benchmark was supplied.

| Check | Result | Evidence |
|---|---|---|
| Backend unit/API/database/provider | 35 passed (locked Linux image) | test-results.xml |
| Earlier Windows run | 34 passed | Development test execution |
| Frontend contracts and chart component | 3 passed | src tests, npm test |
| Development browser/accessibility suite | 5 passed | browser-results.json |
| Docker browser/accessibility suite | 4 passed, admin fixture intentionally skipped | container-browser-results.json |
| Overview axe scan | 0 violations, 1 incomplete contrast check | accessibility-results.json |
| TypeScript / production build | Passed; large ECharts bundle warning | Vite and Docker builds |
| Dependency audit | 0 npm vulnerabilities reported | npm audit |
| Compose startup | API/Mongo healthy, worker/frontend running, HTTP 200 readiness | local port 8088 |
| Presentation | 11 slides; package/layout/font/import validated and visually inspected | PPTX, slide previews |
| PDF report | Rendered and visually inspected | Report PDF |

Coverage includes exact money/null handling, product identity, missing timestamps, CSV/JSON/JSONL, real MongoDB idempotency and immutability, quarantine, cutoff exclusion, current-day exclusion, currency isolation, sparse-history and constant-series abstention, completed analog outcomes, chronological forecast baselines/coverage, owner isolation, CSRF/admin enforcement, as-of selection, alert deduplication/pause/re-arm, leased job recovery, URL allowlist, partial downloads, provider 401/429/503, ambiguous paid submissions, no repeated trigger after a saved snapshot, caps/refresh, background analysis and seeded positive/negative synchronization.

Browser journeys exercise product search/detail, discount quantities, historical comparison, raw evidence, memory/forecast/audit views; persistent watchlists and an actual worker-generated alert; mobile/theme/sparse state; temporary-admin preview/import/re-import dedup; and overview accessibility. The import screenshot contains a generated test fixture, not authentic Amazon evidence. Other application screenshots show labeled synthetic demo data.

Failures corrected during verification included a test fixture module-shadowing error, numeric query parameters rejected by Literal parsing, missing Chromium runtime, two low-contrast text styles, and outdated frontend dependencies, and a NumPy scalar rejected by MongoDB during full analysis persistence. The latter has a BSON serialization regression test. Earlier failed runs are not represented as successful.

`evaluation.json` retains exact per-product metrics, samples, dates, baseline errors and interval coverage. The genuine-discount example scored 86 with about 27.67% historical savings, while its forecast interval covered only about 66.7% of 12 held-out targets. The synthetic synchronization example flags one pair; broad common movement flags none. These are pipeline demonstrations, not evidence of real-world effectiveness.

Bright Data tests are representative HTTP fixtures only. No live paid scraping, email sending, public hosting, remote CI or complete accessibility/security certification is claimed. See `../docs/PROGRESS.md` for scope boundaries.


