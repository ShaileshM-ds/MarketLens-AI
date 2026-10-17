# MarketLens AI

Explainable marketplace intelligence and historical price auditing. React/TypeScript + FastAPI + a separate Python worker + MongoDB. No paid service is needed for the synthetic demonstration.

## Start with Docker

```sh
docker compose up --build -d
```

Open **http://localhost:8088**. Initial deterministic seeding may take several minutes on a slow database. Readiness waits for it. MongoDB is private to the Compose network and persists in a named volume. The application has 36 fictional products and 7,818 synthetic offer observations; the controlled clock is 2026-10-14 UTC.

Use **Take the guided tour**. Start a **private demo session** from the account button to save watches. There is no shared administrator password.

```sh
docker compose exec -e PYTHONPATH=/app backend python scripts/manage.py admin --mode demo
docker compose exec -e PYTHONPATH=/app backend python scripts/manage.py admin --mode imported
```

Each command prompts for your own password. Accounts are isolated by data mode. Create an administrator in live mode only when needed.

## Development

Use Python 3.13, Node 24, and MongoDB 8. Install into a virtual environment:

```sh
python -m venv .venv
# Activate .venv using your shell, then:
pip install -r backend/requirements.txt
cd frontend
npm ci
```

Start a dedicated MongoDB or use your own URI. For example:

```sh
docker run -d --name marketlens-dev -p 127.0.0.1:27028:27017 -v marketlens-dev-data:/data/db mongo:8.0
```

In PowerShell at the repository root:

```powershell
$env:PYTHONPATH="$PWD\backend"
$env:MONGO_URI='mongodb://127.0.0.1:27028'
$env:ALLOWED_ORIGINS='http://127.0.0.1:5174,http://localhost:5174'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8018
```

In a second terminal with the same `PYTHONPATH` and `MONGO_URI`, run `python -m app.workers.main`. In a third terminal, run `npm run dev` from `frontend/`. Open http://127.0.0.1:5174. The Vite proxy routes APIs to 8018.

For POSIX shells, use `export PYTHONPATH="$PWD/backend"`, `export MONGO_URI=mongodb://127.0.0.1:27028`, and `export ALLOWED_ORIGINS=http://127.0.0.1:5174,http://localhost:5174`.

## Import the snapshot

The requested original `amazon_snapshot reference impana` dataset was **not present**. `sources/` was empty and has not been modified. A schema demonstration is in `data/samples/import-example.json`; it is explicitly example content, not historical Amazon evidence.

1. Create an imported-mode administrator, select **Imported data**, and sign in.
2. Open **Data administration**, select JSON, JSONL or CSV, then **Profile and preview**.
3. Map source fields to ASIN, marketplace, price, currency, observation time and optional offer fields. Money input is in major currency units; storage uses integer minor units.
4. Refresh the preview and import. Inspect accepted, duplicate, warning and rejection counts.
5. Missing observation time remains unknown. Such a product appears in the catalog but cannot support timed analyses. Unknown shipping/tax means the total is incomplete.

## Configure Bright Data

Copy `.env.example` to `.env`. Set `BRIGHT_DATA_API_KEY`, your Amazon Products collection-by-URL `BRIGHT_DATA_DATASET_ID`, `BATCH_LIMIT`, and `DAILY_CAP`. Check that your configured scraper accepts the URL-array contract and returns the fields described in `docs/PROVIDER.md`. Only then set `ENABLE_LIVE=1` and recreate backend/worker containers.

Create a live-mode administrator, select Live mode, sign in, and submit allowlisted Amazon URLs under Data administration. Default minimum refresh is 24 hours. Jobs persist provider snapshot IDs; ambiguous submissions require manual reconciliation and are not automatically resubmitted. Live API calls were **not** executed during development. Fixture tests do not verify account-specific scraper output.

## Tests and measured evaluation

```sh
# PYTHONPATH and MONGO_URI set as above; requires real MongoDB
python -m pytest backend/tests -q
python scripts/evaluate.py
cd frontend
npm run build
npm test -- src/api.test.ts src/Chart.test.tsx
npx playwright install chromium
# Running backend, worker and frontend required:
npm run test:e2e
```

Test outputs, evaluation JSON, screenshots, report, diagrams, demo scripts and presentation are in `submission/`. See `docs/PROGRESS.md` for verification status and known limitations. CI configuration is provided; it has not been run on a remote GitHub repository.

## Project layout

- `backend/app/services`: normalization, authentication, alerts, demo and durable jobs.
- `backend/app/analytics/engine.py`: transparent analytical policies and evaluation.
- `backend/app/providers`: Bright Data adapter and local/demo provider contracts.
- `backend/app/workers`: standalone polling, scheduling and alert process.
- `frontend/src`: API-backed interface and accessible chart tables.
- `docs/`: operations, provider contract, methodology and progress.
- `submission/`: academic handover and measured evidence.

No public deployment is claimed. Run the container package on a host with HTTPS and restricted database access before exposing it publicly. See `docs/OPERATIONS.md`.

## Kubernetes

Kustomize manifests for the frontend, backend, background worker, MongoDB storage, internal Services, Ingress, configuration and network policies are in [k8s/](k8s/README.md). The default configuration targets a local cluster; an HTTPS deployment template is also included. Read the guide to prepare images, credentials, storage and ingress before applying it.

Preview the default manifest set from the project root:

```sh
kubectl kustomize k8s
```

See [manifest validation evidence](k8s/VALIDATION.md) for the checks performed and deployment requirements.
