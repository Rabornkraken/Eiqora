# Postgres setup

This folder contains the initialization schema for the data-collection
warehouse described in `docs/data_collection_pipeline_plan_updated_alpaca.md`.

## Start Postgres locally (Docker)

```
docker compose -f data_collection/docker-compose.yml up -d
```

The compose file uses the pgvector-enabled Postgres image.

## Run migrations

```
set -a
source data_collection/config/.env
set +a
alembic -c data_collection/db/alembic.ini upgrade head
```

## Connect

```
psql "postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@localhost:$POSTGRES_PORT/$POSTGRES_DB"
```

You can also set `DATABASE_URL` to override connection details.

## Notes
- Alembic migrations are the source of truth.
- Includes extensions: `vector` (pgvector) and `pg_trgm`.
