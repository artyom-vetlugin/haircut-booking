# Deployment

## Environment Variables

Copy `.env.example` to `.env` and fill in all required values before running the app.

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | yes | PostgreSQL async URL. Must use `postgresql+asyncpg://` scheme. |
| `TELEGRAM_BOT_TOKEN` | yes | Token from [@BotFather](https://t.me/BotFather). |
| `TELEGRAM_MASTER_CHAT_ID` | yes | Telegram chat ID of the master. Used for booking notifications. |
| `ANTHROPIC_API_KEY` | yes | Anthropic API key for Claude. |
| `TELEGRAM_WEBHOOK_URL` | yes (prod) | Public HTTPS URL Telegram will POST updates to, e.g. `https://example.com/webhook/telegram`. |
| `TELEGRAM_WEBHOOK_SECRET` | recommended | Secret token checked in `X-Telegram-Bot-Api-Secret-Token` header. |
| `GOOGLE_CALENDAR_ID` | yes (prod) | ID of the dedicated appointments calendar. |
| `GOOGLE_OAUTH_CREDENTIALS_PATH` | yes (prod) | Absolute path to `gcp-oauth.keys.json`. |
| `ANTHROPIC_MODEL` | no | Defaults to `claude-sonnet-4-6`. |
| `APP_ENV` | no | `development` or `production`. |
| `DEBUG` | no | `true` enables verbose logging and `/docs` UI. |
| `APP_TIMEZONE` | no | IANA timezone for business rule calculations. Defaults to `Asia/Almaty`. |
| `BOOKING_HORIZON_DAYS` | no | How many days ahead clients can book. Defaults to `30`. |
| `MIN_NOTICE_HOURS` | no | Minimum advance notice in hours. Defaults to `2`. |
| `WORKING_HOURS_START` | no | Start of working day (24h, inclusive). Defaults to `9`. |
| `WORKING_HOURS_END` | no | End of working day (24h, exclusive). Defaults to `19`. |
| `WORKING_DAYS` | no | JSON array of weekday numbers (0=Mon). Defaults to `[0,1,2,3,4,5]`. |
| `APPOINTMENT_DURATION_MINUTES` | no | Fixed at `60` for MVP. |

---

## Database Setup

The app uses PostgreSQL 16 with async SQLAlchemy. Schema is managed by Alembic. The database URL is read exclusively from the `DATABASE_URL` environment variable — it is not hardcoded in `alembic.ini`.

### Local (Docker)

```bash
# Start the database container (defined in docker-compose.yml)
docker compose up db -d

# Wait for the health check to pass, then run migrations
docker compose run --rm app alembic upgrade head
```

The `app` service in `docker-compose.yml` overrides `DATABASE_URL` to point to the `db` container (`@db:5432`), so migrations run against the container even if your local `.env` points to `localhost`.

### Production

```bash
# Set DATABASE_URL in your environment, then:
alembic upgrade head
```

Run migrations before starting the app on every deploy. Alembic is idempotent — already-applied migrations are skipped.

To create a new migration after changing ORM models:

```bash
alembic revision --autogenerate -m "describe the change"
# review the generated file in alembic/versions/, then apply:
alembic upgrade head
```

---

## Telegram Webhook Setup

Telegram delivers updates by POSTing to `POST /webhook/telegram`. The endpoint requires:
- An HTTPS URL reachable from the internet (Telegram does not support plain HTTP).
- The URL registered with Telegram via the Bot API.

The app registers the webhook automatically on startup when `TELEGRAM_WEBHOOK_URL` is set. On clean startup the lifespan handler calls:

```python
await bot_client.register_webhook(settings.telegram_webhook_url, settings.telegram_webhook_secret)
```

### Steps

1. Obtain a public HTTPS URL pointing to the running app (reverse proxy, cloud provider, or ngrok for local testing).

2. Set the following in `.env`:
   ```
   TELEGRAM_WEBHOOK_URL=https://your-domain.com/webhook/telegram
   TELEGRAM_WEBHOOK_SECRET=some-random-secret-string
   ```

3. Start the app. The webhook is registered on the first run.

4. Verify registration:
   ```bash
   curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"
   ```
   The response should show `"url"` matching your URL and `"pending_update_count": 0`.

### Webhook Security

The webhook endpoint validates `X-Telegram-Bot-Api-Secret-Token` against `TELEGRAM_WEBHOOK_SECRET`. If the header is missing or does not match, the request is rejected with `403`. Leave `TELEGRAM_WEBHOOK_SECRET` empty only in local/dev environments.

The endpoint always responds `200 OK` to Telegram (even on handler errors) to prevent Telegram from retrying the same update. Duplicate update IDs are tracked in memory and silently dropped.

---

## Google Calendar MCP Setup

The Google Calendar integration uses the `@cocal/google-calendar-mcp` Node.js package as a subprocess. This requires a one-time OAuth authorization before the first production run.

**Prerequisites:** Node.js 18+ must be available on the production host (`npx` must work).

### Step 1 — Create OAuth Credentials

1. Open [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project (or use an existing one).
3. Enable the **Google Calendar API** for the project.
4. Go to **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**.
5. Application type: **Desktop app**.
6. Download the JSON file and save it as `gcp-oauth.keys.json` in a secure location on the server (e.g. `/secrets/gcp-oauth.keys.json`).

### Step 2 — Run the One-Time Auth Flow

```bash
GOOGLE_OAUTH_CREDENTIALS=/secrets/gcp-oauth.keys.json \
    npx @cocal/google-calendar-mcp auth
```

This opens a browser (or prints a URL). Complete the Google sign-in and grant Calendar access. OAuth tokens are saved to disk by the MCP package and reused on subsequent runs.

### Step 3 — Find the Calendar ID

1. Open [Google Calendar](https://calendar.google.com/).
2. Create a dedicated calendar for appointments (or use an existing one).
3. Open **Settings → [Calendar name] → Integrate calendar**.
4. Copy the **Calendar ID** (looks like `abc123@group.calendar.google.com` or an email address for the primary calendar).

### Step 4 — Configure Environment

```
GOOGLE_CALENDAR_ID=your-calendar-id@group.calendar.google.com
GOOGLE_OAUTH_CREDENTIALS_PATH=/secrets/gcp-oauth.keys.json
```

### Step 5 — Enable the Real Adapter

The app currently uses `StubCalendarAdapter` (no-op) in `app/use_cases/deps.py`. Switch to the real adapter for production:

1. In `app/use_cases/deps.py`, replace:
   ```python
   calendar: CalendarAdapter = StubCalendarAdapter()
   ```
   with:
   ```python
   mcp_client = GoogleCalendarMCPClient.from_settings(settings)
   calendar: CalendarAdapter = GoogleCalendarMCPAdapter(mcp_client, timezone=settings.app_timezone)
   ```

2. In `app/integrations/google_calendar_mcp/mcp_client.py`, uncomment the credentials env var line in `from_settings()`:
   ```python
   "GOOGLE_OAUTH_CREDENTIALS": settings.google_oauth_credentials_path,
   ```

3. Wire `mcp_client.start()` / `mcp_client.stop()` into the FastAPI lifespan in `app/main.py`:
   ```python
   await bot_client.initialize()
   await mcp_client.start()
   yield
   await bot_client.shutdown()
   await mcp_client.stop()
   ```

---

## Production Run Steps

### 1. Prepare the environment

```bash
cp .env.example .env
# Fill in all required values in .env
```

### 2. Build and start with Docker Compose

```bash
docker compose up --build -d
```

This builds the app image and starts both the `app` (port 8000) and `db` (port 5432) containers.

### 3. Run database migrations

```bash
docker compose exec app alembic upgrade head
```

### 4. Verify the app is running

```bash
curl http://localhost:8000/health
# {"status": "ok"}

curl http://localhost:8000/health/db
# {"status": "ok"}
```

### 5. Confirm webhook registration

```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"
```

The `"url"` field should match `TELEGRAM_WEBHOOK_URL`. If it is empty, check that the app started successfully and that `TELEGRAM_WEBHOOK_URL` is set in `.env`.

### Running Without Docker

```bash
# Install dependencies
pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Start the server
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

For production omit `--reload`. The app reads all configuration from environment variables — no additional setup files are needed beyond `.env`.

### Logs

The app uses structlog. Set `DEBUG=true` to enable verbose output. In production (`DEBUG=false`) only `WARNING`-level and above are emitted by default. The `/docs` Swagger UI is disabled in non-debug mode.
