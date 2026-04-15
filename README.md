# Haircut Booking Bot

Telegram bot backend for haircut appointment scheduling with a single master.

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

The API is available at http://localhost:8000. Swagger UI is at http://localhost:8000/docs (only in DEBUG=true mode).

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

| Method | Path        | Description                          |
|--------|-------------|--------------------------------------|
| GET    | /health     | Liveness probe                       |
| GET    | /health/db  | Readiness probe (verifies DB access) |
| GET    | /docs       | Swagger UI (debug mode only)         |

## Project Structure

```
app/
├── api/                    # HTTP route handlers (thin, no business logic)
├── core/                   # Config, constants, exceptions, logging
├── db/                     # SQLAlchemy base, ORM models, async session
├── schemas/                # Pydantic request/response models
├── repositories/           # Thin database access layer
├── services/               # Business logic and booking rule validation
├── integrations/
│   ├── telegram/           # Telegram transport adapter
│   ├── anthropic/          # Claude API adapter
│   └── google_calendar_mcp/# Google Calendar MCP adapter
├── use_cases/              # User-facing flow orchestration
├── tools/                  # Claude-callable tool definitions
└── tests/                  # Unit, integration, and e2e tests
alembic/                    # Database migrations
```

## Architecture Notes

- Business logic lives exclusively in `services/` — route handlers and repositories stay thin.
- All external integrations (Telegram, Claude, Google Calendar) are isolated behind adapter interfaces in `integrations/`.
- Claude is never the source of truth for booking state; all state changes go through the service layer.
- All datetimes are timezone-aware.
- Booking rules (horizon, notice window, working hours) are configurable via environment variables.

## Environment Variables

See `.env.example` for the full list. Required variables to start:

| Variable                | Description                          |
|-------------------------|--------------------------------------|
| `DATABASE_URL`          | PostgreSQL async URL (`postgresql+asyncpg://...`) |
| `TELEGRAM_BOT_TOKEN`    | Bot token from @BotFather            |
| `TELEGRAM_MASTER_CHAT_ID` | Telegram chat ID of the master     |
