# CLAUDE.md

## Project Overview

This repository contains a Python backend for a Telegram-based haircut appointment bot.

Product scope for MVP:
- one haircut master only
- clients interact through Telegram
- master uses a dedicated Google Calendar for appointments
- appointment duration is always exactly 1 hour
- bot communication language is Russian
- Claude is used as a controlled assistant for intent recognition, tool orchestration, and Russian-language replies
- business logic must remain deterministic and validated by backend services

Primary goals:
- allow clients to book, reschedule, cancel, and view one future appointment
- notify the master in Telegram about booking changes
- store appointment records in PostgreSQL
- mirror booked appointments in the dedicated Google Calendar via Google Calendar MCP

## Architecture Principles

- Keep business logic independent from Telegram, Claude, and Google Calendar MCP details.
- Claude must never be the source of truth for booking state.
- Google Calendar is the operational calendar for appointments, but the backend must also persist normalized local records.
- All booking rules must be enforced by backend services, not by prompts.
- Keep integrations behind internal adapter interfaces so they can be replaced without rewriting core logic.
- Prefer simple, readable code over abstract or enterprise-heavy patterns.

## Tech Stack

- Python 3.11+
- FastAPI
- PostgreSQL
- SQLAlchemy 2.0
- Alembic
- Pydantic
- Telegram bot integration in webhook mode
- Anthropic Claude API / SDK
- Google Calendar MCP adapter
- Docker for local development

## Repository Structure

The intended structure is:

- `app/api/` for HTTP endpoints and webhook handlers
- `app/core/` for config, constants, logging, shared utilities, and exceptions
- `app/db/` for database setup and ORM models
- `app/schemas/` for Pydantic schemas and DTOs
- `app/repositories/` for thin database access classes
- `app/services/` for business logic and validation
- `app/integrations/telegram/` for Telegram transport concerns
- `app/integrations/anthropic/` for Claude integration
- `app/integrations/google_calendar_mcp/` for Google Calendar MCP integration
- `app/use_cases/` for user-facing flow orchestration
- `app/tools/` for Claude-callable tool definitions
- `app/tests/` for unit, integration, and e2e tests

## Core Business Rules

- Appointment duration is fixed at 60 minutes.
- There is only one master in MVP.
- The system uses one dedicated Google Calendar for appointments only.
- One client may have at most one active future appointment at a time unless requirements are explicitly changed.
- Booking horizon, minimum notice, working days, and working hours must be configurable.
- The bot must communicate with users in Russian by default.
- Do not invent availability.
- Do not claim a booking was created, changed, or cancelled unless the corresponding service operation succeeded.

## Telegram UX Rules

- Prefer button-driven flows for the primary UX.
- Free-text handling is a fallback, not the main path.
- Keep user-facing Russian messages concise, polite, and natural.
- Main user actions:
  - `Записаться`
  - `Моя запись`
  - `Перенести запись`
  - `Отменить запись`
  - `Связаться с мастером`

## Claude Integration Rules

- Claude may help with:
  - recognizing Russian user intent
  - extracting likely date/time meaning from Russian free text
  - selecting backend tools
  - producing concise Russian replies

- Claude must not:
  - invent available slots
  - bypass booking validations
  - directly manipulate the database
  - directly mutate calendar state outside approved backend tools
  - state that booking actions succeeded before tool/service confirmation

- Claude tools must call service-layer methods, not repositories directly.
- When user intent is unclear, prefer a short clarification in Russian or show menu options.

## Calendar Integration Rules

- All appointment calendar operations must go through the internal Google Calendar antegration layer.
- Store and use normalized internal calendar models in the rest of the application.
- Persist `google_event_id` in local appointment records.
- Treat external integration failures as recoverable errors and surface safe messages to users.

## Coding Standards

- Use type hints everywhere practical.
- Prefer explicit, readable code over clever shortcuts.
- Keep functions focused and small.
- Avoid unnecessary abstraction.
- Use timezone-aware datetimes only.
- Put validation and business rules in services, not in route handlers or repositories.
- Keep repositories thin.
- Keep API handlers thin.
- Do not mix Telegram transport formatting with business logic.
- Do not hardcode secrets, chat IDs, tokens, or calendar IDs in code.

## Testing Expectations

Before considering a task complete, cover the most important logic with tests where practical.

Prioritize tests for:
- booking rule validation
- slot generation
- overlap detection
- one active appointment rule
- appointment create/reschedule/cancel flows
- integration boundaries around calendar adapter behavior

Prefer:
- unit tests for business rules
- integration tests for services and repositories
- lightweight end-to-end tests for key Telegram flows

## Data and Safety Rules

- Minimize stored client personal data.
- Never log secrets or access tokens.
- Never expose internal errors directly to Telegram users.
- Use audit logs for booking state changes.
- Keep user-facing error messages short and safe.

## Workflow Expectations for Claude Code

When making changes:
1. Read the relevant files before editing.
2. Preserve the existing architecture and naming patterns.
3. Make the smallest coherent change that solves the task.
4. Update or add tests when changing important logic.
5. Do not introduce unrelated refactors.
6. Explain assumptions clearly when requirements are ambiguous.

When implementing features:
- start from service and domain boundaries
- keep external integrations isolated
- prefer deterministic behavior first
- add Claude-driven free-text behavior only on top of stable flows

## Implementation Priorities

Preferred build order:
1. project scaffold and config
2. database models and migrations
3. Telegram webhook and button menu
4. deterministic availability engine
5. appointment service with internal calendar adapter interface
6. Google Calendar MCP adapter
7. master notifications
8. Claude free-text agent layer
9. hardening and deployment

## Definition of Done for MVP

A task is closer to done when:
- code fits the intended architecture
- business rules are enforced in service layer
- Russian Telegram UX remains coherent
- calendar operations go through the adapter layer
- tests cover critical behavior
- no secrets or environment-specific values are hardcoded
