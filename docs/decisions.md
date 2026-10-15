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

### Step 2: database design and migrations (plan approved 2026-10-15, in progress)

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
