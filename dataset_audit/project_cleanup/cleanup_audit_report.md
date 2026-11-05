# MarketLens-AI Project Cleanup Audit

**Audit date:** 2026-11-05  
**Repository:** `C:\Users\Impana Dechamma\MarketLens-AI`  
**Scope:** Read-only file, Git-status, reference, cache, and size audit. No files were deleted or changed by the audit other than creation of this report. MongoDB and Docker were not accessed.

## Summary

- The working tree was already modified before this audit. Its existing changes and untracked files were left untouched.
- The five enriched INR CSVs are in the external folder `C:\Users\Impana Dechamma\final_products_files`, not under the repository root. They were not changed.
- `dataset_audit/` occupies **4,281,236,120 bytes** (~3.99 GiB); `backups/` occupies **1,410,988,111 bytes** (~1.31 GiB).
- The two protected Phase 3C history CSVs alone occupy **4,051,457,190 bytes** (~3.77 GiB). Both remain essential protected artifacts, not cleanup targets.
- `frontend/node_modules/`, local build output, Python bytecode/test caches, and TypeScript build metadata total **239,014,735 bytes** (~227.94 MiB) and are already covered by `.gitignore`.
- The two files in `backups/` have different SHA-256 digests, so neither is an exact duplicate of the other.
- The early Phase 4D partial backup contains three files that byte-match the corresponding files in the protected later backup. It is still an audit artifact and is listed only as a **potentially removable** item requiring approval.
- No old report or script was proven safe to delete. Reports, execution logs, provenance, datasets, and operational scripts are preserved below.

## A. Safe to delete (after explicit approval)

These are regenerable caches/build outputs. Deleting `frontend/node_modules/` means reinstalling frontend dependencies with `npm ci` before frontend tests or builds. All paths below are already ignored by Git.

| Exact relative path | Contents | Size (bytes) | Referenced by app? | Regenerable? | Recommendation |
|---|---:|---:|---|---|---|
| `frontend/node_modules/` | 12,604 files | 236,295,303 | Build/test dependencies; runtime uses the built image | Yes, from `frontend/package-lock.json` | Safe cache cleanup; run `npm ci` before local frontend work |
| `frontend/dist/` | 3 files | 1,448,423 | Generated local build output; Docker rebuilds from source | Yes, `npm run build` | Safe to regenerate |
| `.pytest_cache/` | 5 files | 29,763 | No application reference | Yes, pytest recreates it | Safe to regenerate |
| `backend/.pytest_cache/` | 5 files | 27,094 | No application reference | Yes, pytest recreates it | Safe to regenerate |
| `backend/app/__pycache__/` | 3 files | 68,962 | No; Python bytecode only | Yes, Python recreates it | Safe to regenerate |
| `backend/app/analytics/__pycache__/` | 2 files | 53,650 | No; Python bytecode only | Yes, Python recreates it | Safe to regenerate |
| `backend/app/providers/__pycache__/` | 3 files | 7,813 | No; Python bytecode only | Yes, Python recreates it | Safe to regenerate |
| `backend/app/services/__pycache__/` | 11 files | 191,422 | No; Python bytecode only | Yes, Python recreates it | Safe to regenerate |
| `backend/app/workers/__pycache__/` | 2 files | 8,178 | No; Python bytecode only | Yes, Python recreates it | Safe to regenerate |
| `backend/scripts/__pycache__/` | 1 file | 20,525 | No; Python bytecode only | Yes, Python recreates it | Safe to regenerate |
| `backend/tests/__pycache__/` | 26 files | 858,269 | No; Python bytecode only | Yes, Python/pytest recreate it | Safe to regenerate |
| `scripts/__pycache__/` | 2 files | 5,175 | No; Python bytecode only | Yes, Python recreates it | Safe to regenerate |
| `frontend/tsconfig.tsbuildinfo` | 1 file | 158 | TypeScript incremental-build metadata only | Yes, TypeScript recreates it | Safe to regenerate |

**Total potential cache/build space recovery: 239,014,735 bytes (~227.94 MiB).** This estimate is the sum of the disjoint paths above; no deletion has been performed.

## B. Potentially removable (requires approval)

These are not included in the safe-cleanup savings. They may contain useful historical context or recovery information.

| Exact relative path | Size (bytes) | Reference check | Regenerable? | Reason and recommendation |
|---|---:|---|---|---|
| `backend/app/main.py.before-overview-optimization.bak` | 27,658 | No references found in application, tests, scripts, or documentation search | Not reliably | Historical source snapshot; inspect/compare and approve before removal |
| `backend/app/services/normalize.py.backup` | 25,661 | No references found in application, tests, scripts, or documentation search | Not reliably | Historical source snapshot; preserve unless explicitly approved |
| `patch_ingest.py` | 6,358 | No call/import references found | It is a one-off source-edit script, not a generated artifact | Contains code that edits `backend/app/services/normalize.py`; do not run during cleanup. Remove only if its historical purpose is no longer needed and you approve |
| `dataset_audit/phase4d_product_ownership_crosswalk/backup_20261105T032803Z/` | 22,211 across 3 files | No application reference | It is a partial MongoDB dump, not safely reproducible without another database export | The `prelude.json`, `quarantine.bson`, and `quarantine.metadata.json` files byte-match their counterparts in the protected `backup_20261105T032858Z`. This older partial copy may be redundant, but is audit evidence; approval required |

The Phase 4D execution scripts and logs, old-only exports, product crosswalk, and ownership audit reports were **not** classified as disposable. They document the cleanup and its provenance.

No `.tmp` or `.log` files were found. No other `.bak`/`.backup` files were found beyond the two listed above. No other exact duplicate small reports were established. Repeated report filenames under validation subfolders were not presumed duplicates.

## C. Must preserve

| Exact relative path or path group | Size / observed contents | Reason |
|---|---:|---|
| External `C:\Users\Impana Dechamma\final_products_files\cameras_dataset_enriched.csv` | 832,223 bytes | Approved INR dataset; outside the repository and untouched |
| External `C:\Users\Impana Dechamma\final_products_files\mens_clothing_dataset_enriched.csv` | 967,932 bytes | Approved INR dataset; outside the repository and untouched |
| External `C:\Users\Impana Dechamma\final_products_files\smart_home_dataset_enriched.csv` | 857,400 bytes | Approved INR dataset; outside the repository and untouched |
| External `C:\Users\Impana Dechamma\final_products_files\wearables_dataset_enriched.csv` | 853,392 bytes | Approved INR dataset; outside the repository and untouched |
| External `C:\Users\Impana Dechamma\final_products_files\womens_clothing_dataset_enriched.csv` | 958,855 bytes | Approved INR dataset; outside the repository and untouched |
| `dataset_audit/phase3c_materialized_history/historical_observations_120d.csv` | 2,241,137,670 bytes | Protected 900,000-row generated-history source |
| `dataset_audit/phase3c_materialized_history/historical_observations_120d_backup.csv` | 1,810,319,520 bytes | Explicitly protected history backup |
| `dataset_audit/phase4d_product_ownership_crosswalk/backup_20261105T032858Z/` | 14 files, 207,383,790 bytes | Explicitly protected verified MongoDB backup |
| `backups/marketlens_imported_before_120_days.archive.gz` | 942,735,462 bytes | Database archive; distinct SHA-256 from the other archive |
| `backups/marketlens_imported_verified.archive.gz` | 468,252,649 bytes | Verified database archive; distinct SHA-256 from the other archive |
| `dataset_audit/` reports, provenance files, Phase 4A–4D exports, logs, plans, and audit scripts | 71 files total; ~4.08 GiB including protected history and verified backup | Audit evidence and cleanup provenance. Do not remove the folder wholesale |
| `backend/dataset_audit/phase3b_local_history_validation/historical_generation_report.txt` | 1,434 bytes | Historical validation report |
| `submission/` reports, tests, evaluation, screenshots, slides, PDF, and presentation | 31 files, 2,236,800 bytes | Referenced submission deliverables and measured evidence |
| Application source, tests, docs, scripts, manifests, `.env`, `.env.example`, and deployment files | Existing working project | Required functionality, user configuration, and current uncommitted changes |

The previously observed database state was not rechecked for this audit. No database collection was read or modified here.

## D. Git exclusion candidates

`git status --short` showed `dataset_audit/` and `backups/` as untracked. These large local artifacts can cause memory pressure if `git add -A` attempts to stage them. Excluding them from Git does **not** remove local files. Consider adding precise entries to `.gitignore` (or use `.git/info/exclude` for machine-local policy):

```gitignore
/dataset_audit/phase3c_materialized_history/*.csv
/dataset_audit/phase4d_product_ownership_crosswalk/backup_20261105T032858Z/
/backups/marketlens_imported_before_120_days.archive.gz
/backups/marketlens_imported_verified.archive.gz
```

The older partial Phase 4D backup is intentionally not included in the proposed ignore rules pending a decision on whether to retain it. `frontend/node_modules/`, `frontend/dist/`, Python caches, `.pytest_cache/`, and `*.tsbuildinfo` are already ignored by the existing `.gitignore`; no new rules are needed for them.

The generated history and backup CSVs alone account for **4,051,457,190 bytes** (~3.77 GiB) of local data that should remain untouched but is a strong candidate for Git exclusion. The archives add another **1,410,988,111 bytes** (~1.31 GiB), and the protected Phase 4D MongoDB dump adds **207,383,790 bytes** (~197.8 MiB).

## Approval gate

No deletion or `.gitignore` change has been performed. Cleanup may proceed only after the user explicitly approves the exact paths in sections A and/or B. Protected items in section C will remain untouched regardless of approval.
