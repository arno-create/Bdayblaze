# Bdayblaze Architecture

## Goals

- Reliable and restart-safe celebration delivery.
- Privacy-first storage of birthday data and related server settings.
- Low operational overhead on constrained infrastructure.
- Compact seams for future features such as Quests, Capsules, Surprises, Studio expansion, Timeline, and Analytics.

## Package boundaries

- `domain`
  - Pure date/time calculations, timezone helpers, theme presets, and strict template rendering.
  - No Discord or database objects.
- `repositories`
  - Thin async SQL layer over `asyncpg`.
  - Explicit queries, indexes, and transactions.
- `services`
  - Birthday registration, import/export, settings validation, health checks, diagnostics, and scheduler orchestration.
- `discord`
  - Slash commands, embeds, gateway side effects, top-level info commands, and setup/Celebration Studio interactions.
  - Discord UX stays here; business rules stay in services/domain.
- `db`
  - Connection pool and migration runner.

## Data flow

1. A slash command hits a cog.
2. The cog validates Discord-specific context, permissions, and member/guild scope.
3. The cog delegates to a service.
4. Services call repositories and pure domain helpers.
5. Scheduler claims due work from indexed timestamp columns and durable event rows.
6. Discord side effects execute from persisted event payloads and batch state, then mark records complete.

## Persistence strategy

### `guild_settings`

- One row per guild.
- Stores:
  - announcement channel and default timezone
  - birthday role and celebration mode
  - Celebration Studio presentation fields
  - birthday announcement template
  - birthday DM settings/template
  - anniversary settings/template/channel override
  - large-server controls such as eligibility role, ignore-bots, minimum membership days, and mention suppression threshold

### `member_birthdays`

- One row per `(guild_id, user_id)`.
- Stores:
  - month/day
  - optional year
  - optional timezone override
  - `profile_visibility`
  - next-occurrence scheduler state
  - active birthday-role snapshot data for reliable cleanup
- Uses compact browse indexes on `(guild_id, birth_month, birth_day)` plus visibility-aware browse paths.

### `tracked_member_anniversaries`

- One row per tracked member anniversary in a guild.
- Stores the member's `joined_at_utc`, next occurrence timestamp, and source metadata.
- Keeps anniversary scheduling cheap and bounded without full-guild scanning or privileged member sync.
- Populated by birthday writes, admin member writes/imports, and explicit anniversary sync flows.

### `recurring_celebrations`

- One row per server-defined annual event.
- Stores name, month/day, enabled flag, optional channel override, optional template override, next occurrence timestamp, and a compact `celebration_kind`.
- `celebration_kind='server_anniversary'` is reserved for the single first-class server anniversary record in a guild.
- Server anniversary can also store `use_guild_created_date` so the UX can default to the guild creation date without adding a second scheduler system.
- Purpose-built for annual server events; not a generic cron or RRULE engine.

### `celebration_events`

- Durable idempotency and work queue for Discord side effects.
- Current event kinds:
  - `announcement`
  - `birthday_role_add`
  - `birthday_role_remove`
  - `birthday_dm`
  - `anniversary_announcement`
  - `recurring_announcement`
- Each event stores:
  - `event_key`
  - `event_kind`
  - `scheduled_for_utc`
  - `state`
  - retry metadata
  - compact JSON payload snapshot for delivery

### `announcement_batches`

- One row per grouped announcement batch token.
- Stores channel, scheduled time, send state, and sent message id when known.
- Lets the scheduler dedupe and recover grouped sends without scanning channel history during normal operation.

## Scheduler model

- Query indexed next-due timestamps instead of scanning full tables.
- On startup:
  - reclaim stale `processing` celebration events
  - claim overdue birthday starts, anniversary starts, recurring events, and role removals inside a grace window
  - recover uncertain announcement batches with a strictly bounded fallback history scan
  - execute pending work
- Normal loop:
  - claim newly due birthday starts
  - claim newly due anniversaries
  - claim newly due recurring celebrations
  - claim newly due role removals
  - execute pending events
  - sleep until the next indexed due timestamp or a bounded max sleep

## Delivery and diagnostics

- Permission and readiness checks are centralized so setup, previews, health checks, and live delivery use the same wording and blockers.
- Admin-heavy embeds use shared budget enforcement so setup, Celebration Studio, dry runs, health checks, import previews, and recurring-event summaries do not exceed Discord field or total embed limits.
- Diagnostics cover:
  - missing `View Channel`
  - missing `Send Messages`
  - missing `Embed Links`
  - missing `Manage Roles`
  - deleted channels/roles
  - managed/default/hierarchy role issues
  - DM unavailable
  - invalid template/media configuration
  - eligibility exclusions such as ignored bots, missing eligibility role, and minimum membership age
- Live birthday announcements, birthday DMs, anniversaries, and recurring events all use the same persisted-event model.
- Server anniversary stays on the recurring-announcement scheduler path internally, but renders as its own announcement kind in previews and live sends.

## Reliability choices

- Celebration events are persisted before Discord side effects run.
- Event states are explicit: `pending`, `processing`, `completed`.
- Announcement batches are explicit: `pending`, `sending`, `sent`.
- Failed work retries with bounded backoff and non-sensitive retry metadata.
- Active role cleanup uses a stored role snapshot so admin config changes do not orphan live birthday roles.
- DM failures are recorded as skip outcomes without noisy retry loops.
- Channel-history scans are reserved for narrow stale-send recovery instead of normal dedupe.
- Stale-send recovery is capped at 3 history requests of 10 bot-authored messages each and only searches inside a narrow time window for the exact batch footer token.
- Late recovered announcements may render graceful recovery wording, but dedupe behavior is unchanged.

## Privacy and product decisions

- Birthdays are stored per guild membership, never as a cross-server profile.
- Birth year is optional and hidden by default.
- Visibility is server-scoped: `private` or `server_visible`.
- Admin setup, health, import/export, member-management, and preview flows are ephemeral.
- Non-admin browse flows never expose birth year and respect visibility settings.
- Logs and diagnostics never include raw birth dates, birth years, or raw custom template bodies.
- Message Content intent is not used.
- There is no inactivity-based eligibility in this pass because the architecture does not maintain a safe, low-cost activity signal.

## Extension seams

The current shape is intentionally compact but extensible:

- `celebration_mode` remains small while leaving room for future announcement styles.
- `AnnouncementStudioPresentation` can expand without turning into an arbitrary embed-builder.
- Celebration Studio sections can grow without reintroducing one giant panel, because the current UI is already split into bounded pages and compact modal-return flows.
- `celebration_events.payload` can carry future capsule/card/drop metadata.
- `tracked_member_anniversaries` provides a clean seam for richer timeline or anniversary features later.
- `recurring_celebrations` is purpose-built now but can back future server milestone experiences.
