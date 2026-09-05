# SignalForge — AI Investment Research Agent PoCs

SignalForge now contains all six sequential PoCs: Earnings Research, living Investment Thesis, Supply Chain Detective, Research Debate, Autonomous Analyst, and the Evaluation Arena at `http://localhost:3000/arena`. Results are research aids, not investment advice.

## Quick start (no API key required)

Requires Python 3.12+ and Node 20+.

```bash
cd apps/api
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

In a second terminal:

```bash
cd apps/web
npm install
npm run dev
```

Open http://localhost:3000 for Earnings Research and http://localhost:3000/thesis for the Thesis workspace. The default deterministic provider uses curated, offline official-document fixtures and makes the complete demo reproducible. Set `MODEL_PROVIDER=openai` and `OPENAI_API_KEY` to use Responses API structured output.

Run tests with `cd apps/api && pytest`. Run the full stack with `docker compose up --build`.

Run a terminal demo with `cd apps/api && python -m app.cli --company nvidia --period FY2025-Q4 --language zh-TW`.

## Architecture

- `apps/api`: FastAPI API, SQLAlchemy persistence, staged research harness, providers, tools and evals.
- `apps/web`: Next.js run launcher and report/trace/eval explorer.
- `skills`: versioned declarative agent skills. Later PoCs remain disabled in `config/features.json`.
- `docs`: API and PoC acceptance documentation.

The default SQLite/filesystem-object-store mode is for zero-setup evaluation. Compose switches the same storage port to PostgreSQL and the MinIO S3-compatible API, while tests remain deterministic and offline.
