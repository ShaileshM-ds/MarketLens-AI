# Deployment and operations

## Local lifecycle

`docker compose up --build -d` builds and starts MongoDB, API, worker and frontend. `docker compose logs -f backend worker` displays service logs. `docker compose down` stops services while retaining the volume. Do not use `down -v` unless intentionally deleting all local data.

The frontend is bound to loopback port 8088. MongoDB has no host port. API readiness pings MongoDB. The API waits for initial synthetic seeding; the worker waits for API health. The frontend nginx routes `/api/` to `backend:8000`; browser code never receives database or provider credentials.

## Container hosting route

Provision a Linux VM or container service supporting long-running Python processes and persistent MongoDB storage. Install Docker and Compose, copy the source and private `.env`, then run Compose. Put a TLS reverse proxy in front of the frontend. Set `PRODUCTION=1`, set `ALLOWED_ORIGINS` to the exact HTTPS origin, and expose only the reverse proxy. Do not expose Docker, MongoDB, or the development Vite server to the Internet.

Create individual administrator accounts interactively. Public visitors can create consumer accounts and isolated demo watchlists; no reusable administrator credential is bundled. Keep paid collection disabled for a public demonstration unless a trusted admin deliberately enables and caps it. Review rate limits and deploy perimeter request limits for larger public traffic.

The supplied configuration is a student-scale deployment, not a claim of audited production readiness. Add backup monitoring, storage quotas, resource limits and centralized observability before sustained public use. Imports enqueue affected-product analyses for the worker. Custom requested analyses are also calculated by the API and persisted; high-volume workloads need more caching and throughput testing.

## MongoDB Atlas

Create a dedicated database user with access only to the three `DB_PREFIX` databases. Configure a network allowlist for the container host; do not allow all IP addresses. Use the Atlas `mongodb+srv://` URI in `MONGO_URI`, stored as an environment secret. Preserve TLS defaults. The local MongoDB service can remain unused or be removed from your deployment-specific override. Credentials and cloud deployment were not supplied or tested.

## Backup and restore

For local Compose, create a database archive inside the container and copy it out:

```sh
docker compose exec mongo mongodump --archive=/tmp/marketlens.archive --gzip
docker compose cp mongo:/tmp/marketlens.archive ./marketlens.archive
```

Restore to an isolated verification environment first:

```sh
docker compose cp ./marketlens.archive mongo:/tmp/marketlens.archive
docker compose exec mongo mongorestore --archive=/tmp/marketlens.archive --gzip
```

No `--drop` is included: replacing an existing database should be a deliberate operator action. Back up all mode databases together when sessions and personal watches need preservation. Atlas uses its configured backup policy instead.

## Worker recovery

Queued, retry and polling jobs are atomically claimed. Active jobs have three-minute leases; expired working jobs can be reclaimed. Expired submission jobs require manual review to avoid duplicate paid requests. Status/download retries have exponential delay plus jitter and a five-attempt bound. Alert IDs combine owner, watch, rule and observation; a unique index prevents duplicate delivery. Conditions re-arm after becoming false; cooldown is 24 simulated or real hours.

## Secrets and dependencies

`.env` is ignored and must never be committed. The frontend uses `package-lock.json`. Python direct requirements are pinned; `requirements.lock` captures resolved dependencies from the verified environment. Audit and update packages before deployment. No external fonts, images, email or LLM services are needed at runtime.
