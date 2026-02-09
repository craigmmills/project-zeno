# Global Nature Watch Agent

Language Interface for Maps & WRI/LCL data APIs.

## Project overview

The core of this project is an LLM powered agent that drives
the conversations for Global Nature Watch. The project is fully
open source and can be ran locally with the appropriate keys
for accessing external services.

### Agent

Our agent is a simple ReAct agent implemented in Langgraph. It
uses tools. The tools at a high level do the following things

- Provide information about its capabilities
- Retrieve areas of interest
- Select appropriate datasets
- Retrieve statistics from the WRI analytics api
- Generate insights including charts from the data

The LLM to use is plug and play, we rely mostly on Sonnet & Gemini
for planning and tool calling.

For detailed technical architecture, see [Agent Architecture Documentation](docs/AGENT_ARCHITECTURE.md).

### Infrastructure

To enable that, the project relies on a set of services being deployed with it.

- eoAPI to provide access to the LCL data in a STAC catalog and serving tiles
- Langfuse for tracing of the agent interactions (optional for local development)
- PostgreSQL with PostGIS for the API data and geographic search of AOIs
- FastAPI deployment for the API

All these services are being managed and deployed through our deploy
repository at [project-zeno-deploy](https://github.com/wri/project-zeno-deploy)

### Frontend

The frontend application for this project is a nextjs project
that can be found at [project-zeno-next](https://github.com/wri/project-zeno-next)

### Evals

We have an evaluation framework we use to do end-to-end testing of the
agent on the deployed API. The framework can be found in the [gnw-evals](https://github.com/wri/gnw-evals) repository.

### STAC

We have a set of scripts to ingest STAC data into the eoAPI deployment. The ingestion code
for STAC can be found in the [gnw-stac](https://github.com/wri/gnw-stac) repository.

## Dependencies

- [uv](https://docs.astral.sh/uv/getting-started/installation/) - Python package manager
- [docker](https://docs.docker.com/) - For running PostgreSQL locally

## Quick Start

```bash
# 1. Clone and install dependencies
git clone git@github.com:wri/project-zeno.git
cd project-zeno
uv sync

# 2. Setup environment files (see Environment Configuration below)
cp .env.example .env
# Create .env.local with your API keys and local overrides

# 3. Start infrastructure and run migrations
make up
cd db && uv run alembic upgrade head && cd ..

# 4. Build dataset embeddings (requires GOOGLE_API_KEY)
mkdir -p data
uv run python src/ingest/embed_datasets.py

# 5. Ingest geographic data (optional but recommended, ~2.5GB download)
uv run python src/ingest/ingest_gadm.py

# 6. Start the application
make api      # Terminal 1: API on http://localhost:8000
make frontend # Terminal 2: Frontend on http://localhost:8501
```

## Local Development Setup

### 1. Clone and install dependencies

```bash
git clone git@github.com:wri/project-zeno.git
cd project-zeno
uv sync
source .venv/bin/activate
```

### 2. Environment configuration

Create your environment files:

```bash
cp .env.example .env
```

Then create `.env.local` with your local development overrides. This file takes precedence over `.env`:

```bash
# .env.local - Local development overrides

# Database connection (Docker PostgreSQL)
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/zeno-data
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5434/zeno-data_test

# API Keys (required)
GOOGLE_API_KEY=your-google-api-key
ANTHROPIC_API_KEY=your-anthropic-api-key  # Optional, for Sonnet/Haiku
OPENAI_API_KEY=your-openai-api-key        # Optional, for GPT models

# Mapbox (required for map visualizations)
MAPBOX_API_TOKEN=your-mapbox-token

# GFW Data API (required for analytics)
GFW_DATA_API_KEY=your-gfw-api-key

# Dataset embeddings (must match the generated file name)
DATASET_EMBEDDINGS_DB=gnw-dataset-index-gemini-v1

# Model configuration
MODEL=gemini-flash
SMALL_MODEL=gemini-flash
EOAPI_BASE_URL=https://eoapi.globalnaturewatch.org

# Local service URLs
API_BASE_URL=http://localhost:8000
LOCAL_API_BASE_URL=http://localhost:8000
STREAMLIT_URL=http://localhost:8501

# Auth settings for local development
COOKIE_SIGNER_SECRET_KEY=local-dev-secret-key
ALLOW_PUBLIC_SIGNUPS=true
ALLOW_ANONYMOUS_CHAT=true
NEXTJS_API_KEY=local-dev-api-key

# Langfuse (optional - use fake keys to disable)
LANGFUSE_SECRET_KEY=fake-secret-key
LANGFUSE_PUBLIC_KEY=fake-public-key
LANGFUSE_HOST=http://localhost:3000
```

### 3. Start infrastructure services

```bash
make up       # Start Docker services (PostgreSQL on ports 5433/5434)
```

This starts:
- PostgreSQL (main database) on port 5433
- PostgreSQL (test database) on port 5434

### 4. Run database migrations

**Important:** Migrations must be run from the `db` directory:

```bash
cd db
uv run alembic upgrade head
cd ..
```

### 5. Build dataset RAG database

Our agent uses a RAG database to select datasets. Build it locally (requires `GOOGLE_API_KEY` with Generative AI API enabled):

```bash
mkdir -p data
uv run python src/ingest/embed_datasets.py
```

This creates `data/gnw-dataset-index-gemini-v1`.

As an alternative, the current production table can also be retrieved from S3 if you have the corresponding access permissions:

```bash
aws s3 sync s3://zeno-static-data/ data/
```

### 6. Ingest geographic data

After starting the database, ingest the geographic boundaries for area-of-interest searches:

| Dataset | Download Size | Description |
|---------|--------------|-------------|
| GADM | ~2.5 GB | Administrative boundaries (countries, states, cities) |
| KBA | ~2 GB | Key Biodiversity Areas |
| Landmark | ~2 GB | Indigenous and Community Lands |
| WDPA | ~10 GB | World Database on Protected Areas |

```bash
# GADM is recommended for basic functionality
uv run python src/ingest/ingest_gadm.py

# Optional - run if you need these specific area types
uv run python src/ingest/ingest_kba.py
uv run python src/ingest/ingest_landmark.py
uv run python src/ingest/ingest_wdpa.py  # Large download, skip if not needed
```

GADM ingestion downloads a 2.5GB zip file, extracts to a 4.6GB GeoPackage, and loads ~400,000 geographic records into PostGIS.

### 7. Start application services

```bash
make api      # Run API locally (port 8000)
make frontend # Run Streamlit frontend (port 8501)
```

Or start everything at once:

```bash
make dev      # Starts API + frontend (requires infrastructure already running)
```

### 8. Access the application

- Frontend: <http://localhost:8501>
- API: <http://localhost:8000>
- API Docs: <http://localhost:8000/docs>

## Development Commands

```bash
make help     # Show all available commands
make up       # Start Docker infrastructure
make down     # Stop Docker infrastructure
make api      # Run API with hot reload
make frontend # Run frontend with hot reload
make dev      # Start full development environment
make test     # Run tests
make clean    # Clean up containers and volumes
```

## Testing

### API Tests

Running `make up` will bring up a test database on port 5434. The tests look for a `TEST_DATABASE_URL` environment variable.

```bash
uv run pytest tests/api/
```

## Environment Files

- `.env` - Base configuration (copy from `.env.example`)
- `.env.local` - Local development overrides (create manually, takes precedence)

The system loads `.env` first, then overrides with `.env.local` for local development.

## Troubleshooting

### "Anonymous chat access is disabled"

Add `ALLOW_ANONYMOUS_CHAT=true` to your `.env.local` file.

### Database connection errors

1. Ensure Docker is running: `docker ps`
2. Check PostgreSQL is up: `docker compose -f docker-compose.dev.yaml ps`
3. Verify the port in `DATABASE_URL` matches docker-compose (default: 5433)

### "relation does not exist" errors

Run database migrations:
```bash
cd db && uv run alembic upgrade head && cd ..
```

### Dataset embeddings errors

1. Ensure `GOOGLE_API_KEY` is set and has Generative AI API enabled
2. Ensure `DATASET_EMBEDDINGS_DB=gnw-dataset-index-gemini-v1` is in `.env.local`
3. Check that `data/gnw-dataset-index-gemini-v1` exists

### Langfuse connection errors

Langfuse is optional for local development. To disable the warnings, ensure these are in `.env.local`:
```
LANGFUSE_SECRET_KEY=fake-secret-key
LANGFUSE_PUBLIC_KEY=fake-public-key
```

## Optional: Local Langfuse Setup

For tracing agent interactions locally:

1. Clone and start Langfuse:
   ```bash
   cd ..
   git clone https://github.com/langfuse/langfuse.git
   cd langfuse
   docker compose up -d
   ```

2. Access Langfuse at <http://localhost:3000>
   - Create an account
   - Create a new project
   - Copy the API keys

3. Update your `.env.local`:
   ```bash
   LANGFUSE_HOST=http://localhost:3000
   LANGFUSE_PUBLIC_KEY=pk-lf-your-public-key
   LANGFUSE_SECRET_KEY=sk-lf-your-secret-key
   ```

## CLI User Management

For user administration commands (making users admin, whitelisting emails), see [CLI Documentation](docs/CLI.md).
