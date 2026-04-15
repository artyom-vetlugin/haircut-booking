# Haircut Booking Bot

Telegram bot backend for haircut appointment scheduling with a single master.

Clients interact through Telegram buttons and free text. The backend enforces all booking rules, persists records in PostgreSQL, and mirrors appointments in a dedicated Google Calendar via Google Calendar MCP.

## Tech Stack

- Python 3.11, FastAPI, uvicorn
- PostgreSQL 16, SQLAlchemy 2.0 async, Alembic
- python-telegram-bot 21 (webhook mode)
- Anthropic Claude API (free-text intent recognition)
- `@cocal/google-calendar-mcp` via MCP stdio subprocess

## Local Development

### Prerequisites

- Docker and Docker Compose, **or** Python 3.11+ with a local PostgreSQL instance

### Quick Start with Docker

```bash
# 1. Copy and configure environment
cp .env.example .env
# Edit .env — set TELEGRAM_BOT_TOKEN and TELEGRAM_MASTER_CHAT_ID at minimum

# 2. Start services
docker compose up --build

# 3. Run database migrations
docker compose exec app alembic upgrade head
```

The API is available at `http://localhost:8000`. Swagger UI is at `http://localhost:8000/docs` (only when `DEBUG=true`).

### Running Without Docker

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. Install dev dependencies
pip install -r requirements-dev.txt

# 3. Configure environment
cp .env.example .env
# Edit .env — update DATABASE_URL to point to your local PostgreSQL instance

# 4. Run migrations
alembic upgrade head

# 5. Start the server
uvicorn app.main:app --reload
```

### Running Tests

```bash
pytest
# With coverage report
pytest --cov=app --cov-report=term-missing
```

## API Endpoints

| Method | Path                  | Description                                     |
|--------|-----------------------|-------------------------------------------------|
| GET    | `/health`             | Liveness probe                                  |
| GET    | `/health/db`          | Readiness probe (verifies DB access)            |
| POST   | `/webhook/telegram`   | Telegram webhook receiver (secret token checked) |
| GET    | `/docs`               | Swagger UI (debug mode only)                    |

## Project Structure

```
app/
├── api/                    # HTTP route handlers (thin, no business logic)
├── core/                   # Config, constants, exceptions, logging, states
├── db/                     # SQLAlchemy base, ORM models, async session
├── schemas/                # Pydantic models (availability, slots)
├── repositories/           # Thin database access layer
├── services/               # Business logic and booking rule validation
├── integrations/
│   ├── telegram/           # PTB webhook handler, state machine, keyboards
│   ├── anthropic/          # Claude client, agent service, tool definitions
│   └── google_calendar_mcp/# CalendarAdapter interface + MCP adapter + stub
├── use_cases/              # User-facing flow orchestration, DI factory
├── tools/                  # Claude-callable tool implementations
└── tests/                  # Unit, integration, and e2e tests
alembic/                    # Database migrations
docs/                       # Architecture and deployment documentation
```

## Environment Variables

See `.env.example` for the full list with descriptions. Minimum required to start:

| Variable                   | Description                                        |
|----------------------------|----------------------------------------------------|
| `DATABASE_URL`             | PostgreSQL async URL (`postgresql+asyncpg://...`)  |
| `TELEGRAM_BOT_TOKEN`       | Bot token from @BotFather                          |
| `TELEGRAM_MASTER_CHAT_ID`  | Telegram chat ID of the master                     |
| `ANTHROPIC_API_KEY`        | Anthropic API key (required for free-text handling)|

For production also set `TELEGRAM_WEBHOOK_URL`, `TELEGRAM_WEBHOOK_SECRET`, `GOOGLE_CALENDAR_ID`, and `GOOGLE_OAUTH_CREDENTIALS_PATH`.

## Architecture Notes

- Business logic lives exclusively in `services/` — route handlers and repositories stay thin.
- All external integrations (Telegram, Claude, Google Calendar) are isolated behind adapter interfaces in `integrations/`.
- Claude is never the source of truth for booking state. It assists with intent recognition and Russian replies; all state changes go through the service layer.
- Button-driven flows are the primary UX. Free-text handling via Claude is a fallback only.
- All datetimes are timezone-aware. Booking rules (horizon, notice window, working hours) are configurable via environment variables.
- The Google Calendar MCP adapter is currently wired to `StubCalendarAdapter` (no-op). See the deployment docs to switch to the real adapter.

See [`docs/architecture.md`](docs/architecture.md) for a detailed layer-by-layer breakdown and the rationale for keeping Claude out of the source of truth.

See [`docs/deployment.md`](docs/deployment.md) for production setup including Telegram webhook registration, database migrations, and Google Calendar MCP OAuth setup.
