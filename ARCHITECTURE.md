# Bdayblaze MVP Architecture

## Goals

- Reliable and restart-safe birthday celebrations.
- Privacy-first storage with minimal personal data.
- Low operational overhead on constrained infrastructure.
- Clean seams for future premium modules without shipping them now.

## Package boundaries

- `domain`
  - Pure date/time calculations and typed models.
  - No Discord or database objects.
- `repositories`
  - Thin async SQL layer over `asyncpg`.
  - Explicit queries, indexes, and transactions.
- `services`
  - Birthday registration, guild settings, scheduler orchestration, and health checks.
- `discord`
  - Slash commands, embeds, and setup interactions.
  - Keep business logic out of cogs.
- `db`
  - Connection pool and migration runner.

## Data flow

1. A slash command hits a cog.
2. The cog validates Discord-specific context and delegates to a service.
3. The service uses repositories and pure domain logic.
4. The scheduler claims due work from indexed timestamp columns and persisted celebration events.
5. Discord side effects are executed from persisted event records, then marked complete.

## Persistence strategy

### `guild_settings`

- One row per guild.
- Stores channel, timezone, role, toggles, and celebration mode scaffold.

### `member_birthdays`

- One row per `(guild_id, user_id)`.
- Stores month/day, optional year, optional timezone override, privacy defaults, and scheduler state:
  - `next_occurrence_at_utc`
  - `next_role_removal_at_utc`
  - `active_birthday_role_id`

### `celebration_events`

- Durable idempotency and audit queue for Discord side effects.
- Each event stores:
  - `event_key`
  - `event_kind`
  - `scheduled_for_utc`
  - `state`
  - retry metadata
  - JSON payload snapshot

This allows the scheduler to claim future work once, survive restarts, and retry Discord failures without scanning the entire birthday table constantly.

## Scheduler model

- Query the next due timestamp from indexed columns instead of polling the full table.
- On startup:
  - reclaim stale `processing` celebration events
  - claim overdue birthday starts and role removals inside a grace window
  - execute pending work
- Normal loop:
  - claim newly due birthday starts
  - claim newly due role removals
  - execute pending events
  - sleep until the next indexed due timestamp or a bounded max sleep

## Reliability choices

- Birthday start and role-removal are persisted before Discord side effects run.
- Celebration events use explicit states: `pending`, `processing`, `completed`.
- Failed work is retried with bounded backoff.
- Active role removal uses a stored role snapshot so admin config changes do not orphan active birthday roles.

## Privacy and UX decisions

- Birthdays are stored per guild membership, never globally shared.
- Birth year is optional and hidden by default.
- Admin config and health output are ephemeral.
- Upcoming birthdays do not reveal birth year or age.
- Logs and diagnostics never include birth dates or birth years.

## Extension seams

The MVP intentionally reserves room for future modules without hard-coding them:

- `celebration_mode` supports future themed behavior.
- `celebration_events.payload` can carry future capsule/card/drop metadata.
- Services are written against typed models so future premium modules can compose without rewriting cogs.
