# Bdayblaze MVP Architecture

## Goals

- Reliable and restart-safe birthday celebrations.
- Privacy-first storage with minimal personal data.
- Low operational overhead on constrained infrastructure.
- Clean seams for future premium modules without shipping them now.

## Package boundaries

- `domain`
  - Pure date/time calculations, timezone helpers, and safe template rendering.
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
5. Discord side effects are executed from persisted event records and announcement-batch records, then marked complete.

## Persistence strategy

### `guild_settings`

- One row per guild.
- Stores channel, timezone, role, toggles, celebration mode, and an optional custom announcement template.

### `member_birthdays`

- One row per `(guild_id, user_id)`.
- Stores month/day, optional year, optional timezone override, privacy defaults, and scheduler state:
  - `next_occurrence_at_utc`
  - `next_role_removal_at_utc`
  - `active_birthday_role_id`

### `celebration_events`

- Durable idempotency and work queue for Discord side effects.
- Each event stores:
  - `event_key`
  - `event_kind`
  - `scheduled_for_utc`
  - `state`
  - retry metadata
  - JSON payload snapshot for message rendering and role work

### `announcement_batches`

- One row per announcement batch token.
- Stores the channel, scheduled time, send state, and sent message id when known.
- Lets the scheduler decide whether a batch is already sent without scanning channel history on every normal run.

## Scheduler model

- Query the next due timestamp from indexed columns instead of polling the full table.
- On startup:
  - reclaim stale `processing` celebration events
  - claim overdue birthday starts and role removals inside a grace window
  - recover uncertain announcement batches
  - execute pending work
- Normal loop:
  - claim newly due birthday starts
  - claim newly due role removals
  - execute pending events
  - sleep until the next indexed due timestamp or a bounded max sleep

## Reliability choices

- Birthday start and role-removal are persisted before Discord side effects run.
- Celebration events use explicit states: `pending`, `processing`, `completed`.
- Announcement batches use explicit states: `pending`, `sending`, `sent`.
- Failed work is retried with bounded backoff.
- Active role removal uses a stored role snapshot so admin config changes do not orphan active birthday roles.
- Channel-history scans are reserved for narrow stale-send recovery instead of normal dedupe.

## Privacy and UX decisions

- Birthdays are stored per guild membership, never globally shared.
- Birth year is optional and hidden by default.
- Admin setup, health output, and message-template flows are ephemeral.
- Upcoming birthdays do not reveal birth year or age.
- Logs and diagnostics never include birth dates, birth years, or raw announcement-template content.

## Extension seams

The MVP intentionally reserves room for future modules without hard-coding them:

- `celebration_mode` keeps the stored shape small while supporting future announcement styles.
- `celebration_events.payload` can carry future capsule/card/drop metadata.
- Services are written against typed models so future premium modules can compose without rewriting cogs.
