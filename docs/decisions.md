# Decisions log

Decisions agreed between you and Claude, newest phase first. The spec (`docs/spec.md`) is the design; this file records how we've chosen to apply it. If the two disagree, ask before changing either.

## Phase 1: data foundation (decided 2026-10-15)

### Scope

Phase 1 builds what the spec's "Phases" section lists: data sources and ingestion, the data model, and duplicate merging.

Done when:
- a week of clean daily price snapshots exists for one saved search, and
- duplicate merging is correct on at least 95% of hand-labeled listing pairs.

Scope decisions:
- **The daily job stops after duplicate merging.** The spec's daily run also covers pricing, scoring and the agent; those belong to later phases.
- **The MarketCheck weekly backfill is in Phase 1.** It shares MarketCheck's 500 calls a month with the daily pulls; the budget split will be proposed in step 4.
- **VinAudit is out of Phase 1.** The spec uses it only for shortlisted cars, which needs Phase 2's scoring.
- **Label pairs first, then build the merge classifier.** Pass 3 of duplicate merging is trained on hand-labeled pairs, so labeling comes before the classifier.
- You're fine with the laptop and Docker running at 6 a.m. daily for the week of snapshots.

### Tech stack

Follow the spec's "Tech stack, infrastructure and costs" section: Python, FastAPI, Postgres, Alembic migrations, a scheduled worker with a Postgres-backed job queue, and Docker Compose.

### Development environment

- The project lives in WSL at `~/lotwatcher` (Ubuntu-22.04 distro), not on the Windows side.
- Python 3.12 inside Docker; the app and tests run there. uv installs a local Python 3.12 for the editor and ruff only.
- Dependencies are managed with **uv**, which writes a lock file (`uv.lock`) recording exact versions.
- Postgres runs from the official `postgres:17` Docker image.
- Docker Desktop's WSL integration is enabled for Ubuntu-22.04. WSL interop was restored with `/usr/lib/binfmt.d/WSLInterop.conf`, because `systemd=true` was clearing it.

### Phase 1 steps

1. Project setup
2. Database design and migrations
3. Shared source client: rate limiter, retries, monthly call counter, 24-hour cache
4. Source clients: Auto.dev, MarketCheck (including the weekly backfill), NHTSA vPIC, NHTSA recalls and complaints
5. Normalize listings into one schema, and match dealers across sources
6. Price snapshots and delisting (missing 3 days in a row means delisted)
7. Duplicate merging: VIN match, candidate search, scoring, merge log with undo
8. Labeling tool and merge accuracy report
9. Daily job, scheduler and worker container
10. One week of real runs, then check against the done criteria

Each step starts with a short plan for your approval, and tests are written first.

### Step 1: project setup (done 2026-10-15)

Goal: an empty project that starts with one command and passes one test. No database tables or data sources yet.

Files:
```
lotwatcher/
├── .gitignore            # keeps .env, caches and virtual envs out of git
├── .env.example          # variable names only (DATABASE_URL for now)
├── pyproject.toml        # project info, dependencies, ruff and pytest settings
├── Dockerfile            # builds the Python app image
├── docker-compose.yml    # Postgres + the API (the worker is added in step 9)
├── src/lotwatcher/
│   ├── __init__.py
│   └── api/
│       ├── __init__.py
│       └── main.py       # FastAPI app with GET /health
└── tests/
    ├── fixtures/         # recorded API responses go here later
    └── test_health.py
```

- Folders for sources, normalizing, merging, jobs and the database are added in the steps that need them.
- Test first: `GET /health` returns status 200 with `{"status": "ok"}`.
- Run `git init` as part of step 1. Commit only when you ask.

Dependencies (confirmed; uv chooses the latest stable versions and records them in the lock file; show the exact versions before moving on):

| Package | Why | Where |
| --- | --- | --- |
| `fastapi` | Web framework | app |
| `uvicorn` | Server that runs FastAPI | app |
| `pytest` | Tests | dev only |
| `httpx2` | Needed by FastAPI's test client; later likely the HTTP client for the data APIs | dev only for now |
| `ruff` | Lint and format | dev only |

Installed versions (from `uv.lock`): fastapi 0.143.2, uvicorn 0.54.0, pytest 9.1.1, httpx2 2.13.1, ruff 0.17.1. `httpx2` also brings in `truststore` 0.10.5 (uses the operating system's trusted certificates for HTTPS).

Commands:
- Start everything: `docker compose up`
- Run tests: `docker compose run --rm api pytest`
- Lint and format: `uv run ruff check . && uv run ruff format .`
- Rebuild the image (only after `pyproject.toml` or `uv.lock` change): `docker compose build api`

Choices made during step 1 (all approved 2026-10-15):
- **`httpx2` instead of `httpx`.** Starlette 1.8.0 deprecated `httpx` for its test client in favor of `httpx2` (same author, now under the Pydantic organization). The HTTP client for the data sources is still to be confirmed in step 3.
- **Postgres settings in `.env`.** `.env.example` also lists `POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB`, because the `postgres:17` image needs them to start. The local `.env` uses a randomly generated password and is readable only by its owner.
- **Build backend is `uv_build`.** It makes `src/lotwatcher` installable as a package and is used only at install time.
- **Ruff settings.** 100-character lines; rule sets E, F, I (import sorting), UP (modern syntax) and B (likely bugs).
- **Dev tools are in the Docker image**, because tests run inside the container.
- **`src/` and `tests/` are mounted into the api container**, so code and test edits are picked up without rebuilding. This works because the project is installed in editable mode.
- **The api container has a health check** that calls `/health`, so `docker compose up --wait` only reports ready once the API is answering.

### Step 2: database design and migrations (done 2026-10-15)

Goal: the Phase 1 core tables exist in Postgres, created by Alembic migrations, with tests for the rules the database enforces. Python code that uses the tables comes in later steps.

Tables created in step 2: `users` (Phase 1 fields only: id, email, home ZIP), `saved_searches`, `dealers`, `dealer_source_ids`, `vehicles`, `listings`, `price_snapshots`.

Added to fill gaps in the spec:
- `listings.vin`: the VIN the source reported, kept separately from `vehicles.vin`. Needed to undo merges and to check that two different VINs are never merged.
- `dealer_source_ids`: each source's own dealer ID mapped to our dealer, for matching dealers across sources.
- `listings.raw`: the original source data, so normalizing can be re-run without new API calls.
- `listings.vehicle_id` may be empty until the merge step (step 7) fills it in.

Left to later steps: `merge_log` (step 7), the monthly call counter and 24-hour cache (step 3, stored in Postgres), `vehicle_checks` and the recall cache (step 4), `eval_labels` (step 8). Gmail and spending-cap columns on `users` come in Phases 3–4.

Design choices:
- SQLAlchemy 2 with the psycopg 3 driver.
- Tables are defined as Python models. Alembic drafts each migration from them, and every migration is checked by hand.
- Location is plain latitude and longitude, with distance computed by a formula. No PostGIS, so the image stays `postgres:17`.
- IDs are auto-incrementing whole numbers.
- Status is text limited to `active` or `delisted` by a check rule (not a Postgres enum).
- Money is whole dollars as integers.
- Migrations run by hand: `docker compose run --rm api alembic upgrade head`.

Database rules: a VIN is unique when present; `(source, source listing ID)` is unique; `(listing, date)` is unique for snapshots; prices and mileage can't be negative; status must be `active` or `delisted`. "Two different VINs are never merged" is enforced in the merge code (step 7).

New dependencies (approved): `sqlalchemy`, `alembic`, `psycopg[binary]`.

Test database: `lotwatcher_test` in the same Postgres container, set by `TEST_DATABASE_URL`. Each test run drops and recreates it and applies all migrations. The code refuses to do this unless the database name ends in `_test`. Each test runs in a transaction that is rolled back.

Choices made while building step 2 (approved 2026-10-15 unless noted):
- **Price may be empty** on `listings` and `price_snapshots`, for "call for price" listings. Negative prices are still rejected.
- **Database addresses use `postgresql+psycopg://`** so SQLAlchemy picks the psycopg 3 driver. `TEST_DATABASE_URL` was added to `.env.example`.
- Installed versions: sqlalchemy 2.1.4, alembic 1.20.0, psycopg 3.3.6 (with psycopg-binary 3.3.6). Alembic also brings in mako 1.4.3 and markupsafe 3.0.4 (templates for new migration files).
- Small details decided while building, pending your review: dealers have separate `address`, `city`, `state` and `zip` columns plus `latitude` and `longitude`; saved searches have sanity rules (year range in order, max price and mileage not negative, radius positive); every table has `created_at`, and tables that change have `updated_at`; lookup indexes are on `listings.vehicle_id`, `listings.vin`, `listings.dealer_id`, `dealer_source_ids.dealer_id` and `saved_searches.user_id`.
- `alembic.ini` stores no database address (`env.py` reads `DATABASE_URL`). Migration files are named with the date first, and each new one is formatted and lint-fixed by ruff automatically.

Commands added:
- Apply migrations: `docker compose run --rm api alembic upgrade head`
- Draft a migration after changing `models.py`: `docker compose run --rm --user "$(id -u):$(id -g)" -e RUFF_NO_CACHE=true api alembic revision --autogenerate -m "<what changed>"`, then check the file by hand.
- Confirm models and migrations match: `docker compose run --rm api alembic check`

Decided for step 5 (2026-10-15): listings get their own `year`, `make`, `model` and `trim` columns, holding what each source reported. The spec keeps these only on `vehicles`, but the merge candidate search needs them for listings not yet linked to a vehicle. The columns are added by a migration in step 5. Chosen over reading them from `raw` or creating a vehicle for every listing straight away.

### Step 3: shared source client (done 2026-10-15)

Goal: one client that every data source uses for web requests. It handles caching, the monthly budget, pacing and retries, so no source does these itself. No real API is called in step 3.

Request flow: cache check → reserve one call in the budget (stop at the limit) → rate limiter wait → send → on a temporary failure, wait longer and retry from the budget step → cache the successful response.

Decisions:
- **HTTP library: `httpx2`**, moved from dev-only to an app dependency. Its fake transport lets tests return scripted responses without a network.
- **Sync, not async.** Daily volume is small.
- **Retries: our own short code, not `tenacity`.** It retries network errors, 429 and 5xx, up to 3 retries with waits of 1, 2 and 4 seconds, and honours a `Retry-After` header. It never retries other 4xx errors. Every attempt counts against the budget.
- **Two new Postgres tables**, from a new migration: `api_call_counts` (source, month, calls) and `api_cache` (request key, source, response, fetched at, expires at).
- **The budget stops 5% below each cap** (Auto.dev at 950 of 1,000; MarketCheck at 475 of 500). A call is reserved before it is sent, with one database statement that adds 1 only if still under the limit, saved immediately.
- **Month means calendar month in UTC.** Each provider's billing month gets checked in step 4.
- **Alerts for now: a warning in the logs plus an error that stops that source.** Real alerts come with monitoring in Phase 3.
- **API keys** come from environment variables and are never part of a cache key, never stored in the cache, and never logged.
- **Only successful responses are cached.** Cache time is 24 hours by default; a source can choose a different time or "forever" (decoded VINs).
- **Per-source settings** (base address, calls per second, monthly cap, cache time) are Python constants, added in step 4. Unknown limits get checked against each provider's docs then, not guessed.
- **Tests use no network and no real waiting** (a fake clock). Step 3's tests use small made-up responses written inside the tests; recorded real API responses come with each source in step 4.

Built in step 3:
- `src/lotwatcher/sources/`: `client.py` (`SourceClient.get_json`), `budget.py`, `cache.py`, `rate_limit.py`, and `clock.py` (the real clock; tests use a fake one).
- `db/session.py` gained `transaction()`: a short transaction that is saved at once, or nested inside the caller's transaction in tests.
- Migration `9893efd57f47` adds `api_call_counts` (key: source + month) and `api_cache` (key: SHA-256 of source, path and sorted non-secret parameters).
- `httpx2` moved to app dependencies. Ruff setting added: `lotwatcher` is always sorted as first-party.
- Key protection: `httpx2` logs full request URLs (including keys) at INFO level, so the client sets its logger to WARNING. Error messages name only the path and the error type, never the URL. Verified: with the logger line removed, the leak test fails.
- A response that isn't JSON is treated as a failed request, not retried, and not cached.
- Every request has a 30-second timeout; a timeout counts as a network error and is retried. (This and the line above confirmed 2026-10-15.)

- **`Retry-After` limit (approved and built 2026-10-15):** if a server asks for a wait over 60 seconds, the client gives up on that request with a clear error instead of stalling the daily job. Exactly 60 seconds is still waited.
