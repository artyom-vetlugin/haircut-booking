# Architecture

## High-Level Overview

The system is a Telegram-based appointment bot for a single haircut master. Clients interact exclusively through Telegram. The backend enforces all booking rules, persists records in PostgreSQL, and mirrors appointments in a dedicated Google Calendar.

```
┌─────────────────────────────────────────────────────────────────┐
│                          Telegram                               │
│           (client sends messages / presses buttons)             │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTPS webhook
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                        FastAPI App                              │
│  POST /webhook/telegram → secret check → dedup → PTB dispatch  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Telegram Handlers (PTB)                       │
│  State machine: button callbacks + free-text fallback           │
│  Resolves services via make_services() DI factory               │
└───────┬─────────────────────────────────────┬───────────────────┘
        │ button-driven path                  │ free-text fallback
        ▼                                     ▼
┌───────────────────┐               ┌─────────────────────────────┐
│   Use Cases       │               │   HandleFreeTextMessageUseCase│
│   (start, etc.)   │               │   → AgentService (Claude)    │
└───────┬───────────┘               └──────────────┬──────────────┘
        │                                          │ tool calls
        ▼                                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Service Layer                             │
│  BookingRulesService · AvailabilityService · AppointmentService │
│  NotificationService                                            │
└──────────┬──────────────────────────────┬───────────────────────┘
           │                              │
           ▼                              ▼
┌─────────────────────┐       ┌───────────────────────────────────┐
│    Repositories     │       │   CalendarAdapter interface        │
│  (SQLAlchemy async) │       │   └── StubCalendarAdapter (dev)    │
└──────────┬──────────┘       │   └── GoogleCalendarMCPAdapter     │
           │                  │         └── MCP stdio subprocess   │
           ▼                  └───────────────────────────────────┘
┌─────────────────────┐
│    PostgreSQL        │
│  clients            │
│  appointments       │
│  bot_sessions       │
│  audit_logs         │
└─────────────────────┘
```

The bot communicates with users in Russian. There is one master. Appointment duration is always 60 minutes.

---

## Responsibilities by Layer

### `app/api/` — HTTP entry points
Thin FastAPI routers. The webhook router (`integrations/telegram/webhook.py`) validates the Telegram secret token, deduplicates update IDs, and forwards to the PTB dispatcher. Health probes in `api/health.py`. No business logic here.

### `app/integrations/telegram/` — Telegram transport
Owns the PTB `Application` singleton and the booking state machine in `handlers.py`. Translates Telegram updates into service calls and formats Russian replies. Manages session state (stored in `bot_sessions`) to track where a user is in a multi-step flow. Keyboard builders (`keyboards.py`) and message templates (`messages.py`) are pure formatting helpers.

### `app/use_cases/` — Flow orchestration
Thin objects that coordinate services for a single user-visible operation (e.g. `/start`, free-text handling). The DI factory `make_services()` in `deps.py` constructs all service and repository instances around an `AsyncSession`, keeping PTB handlers free of construction details.

### `app/services/` — Business logic (source of truth)
All booking rules live here. Nothing outside this layer enforces them.

| Service | Responsibility |
|---|---|
| `BookingRulesService` | Stateless rule checks: working day/hours, minimum notice, booking horizon |
| `AvailabilityService` | Generates candidate 1-hour slots, filters by rules and busy intervals |
| `AppointmentService` | Full booking lifecycle: one-appointment rule, overlap check, calendar write, DB write, audit log, master notification |
| `NotificationService` | Sends Telegram messages to the master (new/reschedule/cancel events) and to the client when the master cancels or reschedules their appointment |

`AppointmentService` writes to the calendar adapter **before** writing to the database. If the calendar write fails, the booking is aborted. If a subsequent step fails (DB write, notification), the error is propagated or swallowed safely — the calendar event is the primary record for the master.

### `app/repositories/` — Data access
Thin async SQLAlchemy wrappers. One class per ORM model. No business logic. Called only by services.

### `app/integrations/google_calendar_mcp/` — Calendar integration
Isolated behind the `CalendarAdapter` abstract interface. The rest of the application never imports the MCP client directly.

```
CalendarAdapter (ABC)
  ├── StubCalendarAdapter   — no-op, returns fake IDs (dev / test default)
  └── GoogleCalendarMCPAdapter
        └── GoogleCalendarMCPClient  — manages npx @cocal/google-calendar-mcp subprocess
```

`calendar_mapper.py` converts raw MCP JSON shapes to internal `CalendarEvent` / `BusyInterval` models. All MCP errors are translated to `CalendarSyncError` so nothing leaks out of the adapter boundary.

### `app/integrations/anthropic/` — Claude integration
Isolated behind `AgentService`. Runs a tool-use loop (max 5 iterations). Each loop iteration either returns a final Russian text reply or invokes a tool from `app/tools/`. The loop result is a plain string handed back to the Telegram handler. Claude is never given direct database or calendar access.

### `app/tools/` — Claude-callable tools
Five async functions (`get_available_slots`, `get_my_appointment`, `create_booking`, `cancel_appointment`, `reschedule_appointment`). Each calls `AppointmentService` or `AvailabilityService` — never repositories directly. `tool_executor.py` dispatches tool name strings to these functions.

### `app/core/` — Config and cross-cutting concerns
`config.py` (pydantic-settings), `constants.py` (button labels, Russian messages), `exceptions.py` (domain exceptions), `states.py` (session state constants), `logging.py` (structlog setup), `correlation.py` (per-request correlation ID).

### `app/db/` — ORM models and session
Four tables: `clients`, `appointments` (stores `google_event_id`), `bot_sessions` (state + JSON draft payload), `audit_logs`. `session.py` provides `AsyncSessionLocal` for PTB handlers and `get_db` for FastAPI dependency injection.

---

## Why Claude Is Not the Source of Truth

Claude assists with intent recognition and generating Russian replies. It is explicitly not trusted to determine or report booking state.

### The problem with trusting Claude for state

Large language models can hallucinate. Even with a strong system prompt, a model can:
- invent available time slots that do not exist
- misread a tool result and claim a booking was created when the tool returned an error
- confuse the current client's appointment with another
- produce a confident confirmation after a transient tool failure

If any of these happened and the user acted on that reply, the master would be unaware of the appointment, the database would be inconsistent, and the Google Calendar would not reflect reality.

### How the system prevents this

**All writes go through `AppointmentService`.**
Whether the user pressed a button or Claude called `create_booking` via a tool, the same service method runs. That method enforces every business rule in order: one-appointment rule, working hours, minimum notice, horizon check, overlap detection, calendar write, database write, audit log. Claude cannot bypass any of these by wording a reply differently.

**Claude only reads state it received from tool results.**
The system prompt instructs Claude never to state that a booking was created, changed, or cancelled unless the corresponding tool returned success. But more importantly, the tool functions themselves call service methods that either succeed and return a confirmation string, or raise a domain exception that becomes an error string — in both cases Claude only ever sees what the service layer actually produced.

**The database and Google Calendar are mutated only by services.**
Claude calls tools. Tools call services. Services call repositories and the calendar adapter. Claude has no import path to repositories or the MCP client. There is no mechanism by which a Claude reply can write to the database or calendar directly.

**Booking state is derived from the database, not from Claude's context.**
When a user asks "what is my appointment?", the `get_my_appointment` tool queries `AppointmentRepository`. The answer comes from the database. Claude's conversation history is irrelevant.

**Tool results are verifiable.**
Every tool returns a deterministic string based on service output. The audit log captures all booking state changes with timestamps. If a discrepancy is ever suspected, the database is the authoritative record — not the chat history.

### Summary

Claude is used as a controlled assistant: it can suggest an interpretation, select a tool, and draft a reply. But it never decides whether a slot is available, never confirms a booking on its own authority, and never mutates state. All decisions that matter run through the service layer regardless of how the user initiated them.
