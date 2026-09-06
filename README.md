# SignalForge — AI Investment Research Agent PoCs

SignalForge contains six sequential PoCs plus a developer Console. Direct-company workflows accept canonical tickers from the versioned TWSE top-100 and Nasdaq-100 universe; research uses SEC or TWSE/MOPS official data and stores an immutable universe/profile snapshot with every run.

## Quick start (no API key required)

Requires Python 3.12+ and Node 20+.

```bash
# From the repository root. On Windows use: copy .env.example .env
cp .env.example .env
# Fill ADMIN_TOKEN, ADMIN_SESSION_SECRET and SEC_USER_AGENT in .env.
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

Open http://localhost:3000 for Earnings Research and http://localhost:3000/console for the Traditional-Chinese developer Console. The API finds the repository-level `.env` whether it is launched from the root or `apps/api`. Set `ADMIN_TOKEN`, `ADMIN_SESSION_SECRET`, and an SEC-compliant `SEC_USER_AGENT` (application plus contact address); secrets are never returned by APIs or accepted in Agent profile JSON. The deterministic provider still fetches official SEC/TWSE data and composes a template from normalized facts. Set `FIXTURE_MODE=true` for the completely offline NVIDIA/TSMC fixtures used by CI. Set `MODEL_PROVIDER=openai` and `OPENAI_API_KEY` to use Responses API structured output and supported reasoning-summary stream events.

Keep `ADMIN_COOKIE_SECURE=false` for the documented local HTTP setup. Set it to `true` when both the Web app and API are served over HTTPS.

The Console exposes Runs, versioned Agent Profiles, Models, Tools & Skills, Universe status, and the Audit Log. Full run detail includes the immutable profile/universe configuration, Agent DAG, decision or supported reasoning summaries, tool summaries, checkpoints, official source hashes/object references, token usage, cost, retries, and errors. It intentionally never exposes private chain-of-thought.

Run tests with `cd apps/api && pytest`. Run the full stack with `docker compose up --build`.

Run a terminal demo with `cd apps/api && python -m app.cli --company nvidia --period FY2025-Q4 --language zh-TW`.

## Architecture

- `apps/api`: FastAPI API, SQLAlchemy persistence, staged research harness, providers, tools and evals.
- `apps/web`: Next.js run launcher and report/trace/eval explorer.
- `skills`: versioned declarative agent skills. Later PoCs remain disabled in `config/features.json`.
- `docs`: API and PoC acceptance documentation.

The default SQLite/filesystem-object-store mode is for zero-setup evaluation. Compose switches the same storage port to PostgreSQL and the MinIO S3-compatible API, while tests remain deterministic and offline.

## Production deployment

`render.yaml` provisions the FastAPI service and PostgreSQL in Render's Singapore region. The Vercel Web app should set the server-only `API_ORIGIN` to the Render service URL; browser requests remain same-origin and Next.js proxies `/api/*` and `/health`. Leave `NEXT_PUBLIC_API_URL` unset in production. Configure `OPENAI_API_KEY`, `ADMIN_TOKEN`, `ADMIN_SESSION_SECRET`, and `SEC_USER_AGENT` only in Render's secret environment variables.

The checked-in Blueprint uses free Render resources. Free services can cold-start, free PostgreSQL has Render's current retention limits, and `/tmp` object artifacts are ephemeral. Use a paid persistent disk or an external S3-compatible object store before treating stored source artifacts as durable production records.
