# Project Zeno

Global Nature Watch Agent - LLM-powered language interface for maps and WRI/LCL data APIs.

## Architecture

- **Agent**: ReAct agent built with LangGraph (Sonnet & Gemini for planning/tool calling)
- **API**: FastAPI (`src/api/`) on port 8000
- **Frontend**: Streamlit (`frontend/`) on port 8501 (legacy; production uses a separate Next.js repo)
- **Database**: PostgreSQL + PostGIS (`postgis/postgis:17-3.5`)
- **Tracing**: Langfuse (optional locally)

Source layout: `src/agent/`, `src/api/`, `src/ingest/`, `src/shared/`

## Local Development

```bash
make up          # Start db + test-db via docker-compose.dev.yaml (ports 5433/5434)
cd db && uv run alembic upgrade head && cd ..  # Run migrations
make api         # API with hot reload
make frontend    # Streamlit frontend
make test        # pytest
make down        # Stop infrastructure
```

Env files: `.env` (base, from `.env.example`) + `.env.local` (local overrides, takes precedence).

## Key Commands

| Command | What it does |
|---------|-------------|
| `make up` | Start PostgreSQL containers (dev on 5433, test on 5434) |
| `make api` | `uvicorn src.api.app:app --reload` on port 8000 |
| `make test` | `uv run pytest tests/ -v` |
| `make clean` | Tear down containers + volumes |

## Code Quality

- **Linter/formatter**: ruff (line-length 79, `E/F/W/Q/I` rules, `E501` ignored)
- **Pre-commit**: trailing whitespace, yaml check, large file check, private key detection, ruff lint + format
- Python 3.12.8, managed with `uv`

## Testing

- Tests in `tests/` (agent, api, cli, load, tools)
- Uses `TEST_DATABASE_URL` pointing at the test-db container (port 5434)
- pytest-asyncio with `asyncio_mode = "auto"`

## Docker

- Dev infrastructure: `docker-compose.dev.yaml` (db + test-db + migrate)
- Full stack: `docker-compose.yaml` (api, frontend, db, langfuse, clickhouse, minio, redis)
- Database data is bind-mounted to `./database_data` and `./database_data_test`
