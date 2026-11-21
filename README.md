# Keitaro Wrapper

A wrapper over the Admin API of the [Keitaro](https://keitaro.io) tracker:

1. **Campaign creator.** Takes a name, a list of countries and an offer. Creates a Keitaro campaign
   on the chosen domain, group and traffic source with two flows: traffic from those countries is
   redirected to Google, everyone else goes to the offer.
2. **Campaign editor.** Imports an existing campaign and edits the offers of its flows: add,
   remove, restore, delete, pin a share. Edits are kept locally as a draft and sent to Keitaro with
   one "push". Shares of unpinned offers are split equally. Clicks, conversions and revenue are shown
   per offer.

Stack: Python 3.14, FastAPI, SQLAlchemy 2 (async, asyncpg), Alembic, PostgreSQL 16. Frontend: React
19, TypeScript, Vite, Mantine.

## Requirements

- [uv](https://docs.astral.sh/uv/) (installs Python 3.14 by itself, see `backend/.python-version`)
- Node.js 20.19+ or 22.12+ and npm
- Docker with Compose, for PostgreSQL
- A Keitaro tracker URL and an Admin API key (tracker -> profile -> API keys)

## Setup

### 1. Environment

```bash
cp backend/.env.example backend/.env
```

Fill in `KEITARO_URL` (the tracker address without `/admin_api/v1`) and `KEITARO_API_KEY`. The
`POSTGRES_*` variables are read both by the database container and by the app, so the password is
written once.

### 2. Database

```bash
docker compose -f compose.dev.yaml up -d
```

PostgreSQL listens on `127.0.0.1:5432`; data is kept in the `pg_data_dev` volume.

### 3. Backend and migrations

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --port 8000
```

The API is served at `http://127.0.0.1:8000/api`, interactive docs at `http://127.0.0.1:8000/docs`.

Other migration commands, run from `backend/`:

| Command | What it does |
|---|---|
| `uv run alembic current` | Revision the database is at |
| `uv run alembic history` | All revisions |
| `uv run alembic downgrade -1` | Roll back the last migration |
| `uv run alembic revision --autogenerate -m "..."` | New migration from model changes (review it before applying) |
| `uv run alembic check` | Fails if the models and migrations differ |

### 4. Frontend

The backend keeps running in its terminal; open a second one at the repository root:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`. The dev server proxies `/api` to the backend on port 8000.

## Checks

Backend, from `backend/`:

```bash
uv run pytest
uv run ruff check
uv run ruff format --check
uv run mypy
```

Tests use a separate database, `keitaro_wrapper_test`, in the same PostgreSQL container. It is
created on the first run; each test runs in a transaction that is rolled back. Keitaro is mocked,
tests never call a real tracker. If PostgreSQL is not running, database tests are skipped.

Frontend, from `frontend/`:

```bash
npm run check
```

It runs the TypeScript compiler, oxlint and Prettier.

## Project layout

```
backend/
  app/
    core/        settings (.env) and database engine
    keitaro/     Admin API client and its payload models
    services/    business logic: sync, shares, editor, creator
    api/         HTTP routes, request and response schemas
    models.py    database models
  migrations/    Alembic
  tests/         pytest: services against a real database, API wiring, Keitaro client
frontend/
  src/
    pages/       campaign list, editor, creator
    components/  flow card with the offers table
    api.ts       typed client for the backend
compose.dev.yaml PostgreSQL for development
```

## How it works

- **Local copy.** The database keeps only what the editor needs: campaigns, flows, offers in flows
  and an offer catalog. Primary keys are the Keitaro IDs.
- **Sync.** Import and "refresh" read the campaign, its flows and the readable offers from Keitaro,
  and only then change the database, so a Keitaro error leaves the database as it was.
- **Draft.** Every edit changes only the database and marks the flow as having unpushed changes.
  "Push" sends each changed flow with one `PUT /streams/{id}` and checks that Keitaro saved exactly
  what was sent. "Discard" returns the edited flows to their state after the last sync or push,
  then re-reads Keitaro; the discard itself is local and is kept even if Keitaro is unreachable.
- **Offer states.** `active`: in the flow and sent to Keitaro. `removed`: taken out in the wrapper,
  kept so it can be restored. `missing`: was active, but disappeared from Keitaro on sync; can be
  restored. An offer that becomes removed or missing loses its pin. "Delete" removes a removed or missing offer from the wrapper for good.
- **Shares.** Pinned offers keep their share; the rest of 100% is split equally between unpinned
  active offers, and the remainder of the division goes to the first of them in order.
- **Creator.** Keitaro has no transactions: if a flow cannot be created, the new campaign is deleted
  again (Keitaro may only move it to its archive). If only the import after creation fails, the campaign stays and the error names its ID, so
  it can be imported by hand.

## Assumptions and limitations

- Only offers that the API key can read (`GET /offers`) can be added to a flow or chosen in the
  creator. Offers already in a flow that the key cannot read are shown as "Offer #id"; they can be
  removed, restored and pinned, and they are always sent back on push, so Keitaro does not lose
  them.
- Only flows with the `landings` schema carry offers and are editable. Redirect flows are shown
  read-only.
- Refresh is refused while a campaign has unpushed changes: push or discard them first.
- There is no conflict detection: if the same flow is changed in Keitaro after the last sync, push
  overwrites it (last push wins).
- An offer disabled in Keitaro is treated as active and is sent back as active on push.
- Restoring an offer while another offer is pinned at 100% gives the restored offer 0%.
- "Delete" is applied at once and is not part of the draft, so discard does not bring the offer
  back. If Keitaro still has it, the next sync shows it again.
- Pins and offers removed in the wrapper exist only in the wrapper; Keitaro knows nothing about
  them.
- The creator generates the campaign alias from the name plus a random suffix, because Keitaro
  requires an alias unique across the whole tracker.
- There is no authentication: the app is meant to run locally next to one API key.
