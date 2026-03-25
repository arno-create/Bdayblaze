# Bdayblaze Architecture

## Goals

- Reliable and restart-safe celebration delivery.
- Privacy-first storage of birthday data and guild settings.
- Low operational cost on constrained hosting.
- Compact seams for future features without introducing a second worker stack.

## Package boundaries

- `domain`
  - Pure date/time rules, theme presets, placeholder rendering, and media classification.
  - No Discord or database objects.
- `repositories`
  - Thin async SQL layer over `asyncpg`.
  - Explicit queries, indexes, and transactions.
- `services`
  - Birthday flows, experience features, settings validation, content policy, scheduler orchestration, diagnostics, and health.
- `discord`
  - Slash commands, embeds, setup/studio views, gateway delivery side effects, and top-level info commands.
- `db`
  - Pool setup and migration runner.

## Core data flow

1. A slash command hits a cog.
2. The cog validates Discord-specific context and permissions.
3. The cog delegates to a service.
4. Services use repositories plus pure domain helpers.
5. Scheduler claims due work from indexed timestamps and durable event rows.
6. Discord side effects execute from persisted payloads, then records are completed.

## Persistence strategy

### `guild_settings`

One row per guild. Stores:

- announcement routing and default timezone
- birthday role and celebration mode
- Celebration Studio presentation fields
- birthday, DM, and anniversary templates
- anniversary channel override
- eligibility controls
- Studio audit channel

### `member_birthdays`

One row per `(guild_id, user_id)`. Stores:

- month/day
- optional year
- optional timezone override
- `profile_visibility`
- next-occurrence timestamps
- active birthday-role snapshot data

### `tracked_member_anniversaries`

One row per tracked anniversary. Stores `joined_at_utc`, next occurrence, and source metadata so anniversary scheduling stays bounded without full-guild scans.

### `recurring_celebrations`

One row per annual server-defined event. Stores:

- name
- month/day
- enabled flag
- optional channel override
- optional template override
- `celebration_kind`
- `use_guild_created_date`
- next occurrence

`celebration_kind='server_anniversary'` is reserved for the single server-anniversary record in a guild.

### `guild_experience_settings`

One row per guild. Stores opt-in experience toggles and compact thresholds for:

- Birthday Capsules
- Birthday Quests
- Birthday Surprises
- quest wish targets
- quest reaction targets
- optional quest check-in

### `birthday_wishes`

One active unrevealed row per `(guild_id, author_user_id, target_user_id)`. Stores:

- bounded wish text
- optional safe HTTPS link
- reveal/removal/moderation state
- optional resolved celebration occurrence

### `birthday_celebrations`

One row per `(guild_id, user_id, occurrence_start_at_utc)`. Stores:

- late-delivery marker
- shared birthday announcement message id when a public post exists
- capsule reveal state/message id
- revealed-wish counts
- quest progression and completion, including reaction totals and reaction-goal state
- featured marker
- Birthday Surprise selection
- manual Nitro concierge fulfillment state

### `guild_surprise_rewards`

Per-guild weighted reward pool rows for the compact v1 reward types:

- `featured`
- `badge`
- `custom_note`
- `nitro_concierge`

### `celebration_events`

Durable work queue and idempotency layer for Discord side effects.

Current event kinds:

- `announcement`
- `birthday_dm`
- `anniversary_announcement`
- `recurring_announcement`
- `capsule_reveal`
- `role_start`
- `role_end`

### `announcement_batches`

Grouped announcement send state. Used for dedupe and bounded stale-send recovery without normal-operation history scans.

## Scheduler model

- Query indexed next-due timestamps instead of scanning full tables.
- On startup:
  - reclaim stale `processing` work
  - recover overdue birthdays, anniversaries, recurring events, and role removals inside the grace window
  - recover uncertain announcement batches with a bounded fallback scan
- Shared birthday-post reaction quest progress is refreshed outside the scheduler through debounced raw reaction events and explicit status/timeline refreshes.
- Normal loop:
  - claim due work
  - execute pending events
  - sleep until the next due timestamp or a bounded max sleep

## Media validation model

Media handling is intentionally split into two layers:

1. Local classification in `domain.media_validation`
   - distinguishes `direct_media`, `webpage`, `unsupported_media`, `invalid_or_unsafe`, and `needs_validation`
   - rejects unsafe hosts, private IPs, credentials, blocked suffixes, and unsafe URL keywords
   - does not pretend every HTTPS URL is a renderable image

2. Bounded admin-only probing in `services.media_validation_service`
   - used only from Studio Media Tools
   - short timeout
   - small redirect cap
   - HEAD first, then tiny ranged GET/body sniff fallback
   - accepts real image/GIF/WebP content types or matching signatures
   - classifies HTML as webpage content

This keeps live sends cheap while giving admins a safer save/preview flow.

## Moderation and abuse protection

Studio/admin-authored inputs are checked by a deterministic content-policy layer:

- birthday announcement template
- birthday DM template
- anniversary template
- server-anniversary template
- recurring event names and templates
- title override
- footer text
- unsafe media URL patterns

The policy is intentionally limited:

- blocks obvious profanity, NSFW wording, slurs, and harassment-style phrases
- does not use ML moderation
- does not inspect remote image contents

Blocked Studio/admin save attempts can optionally be logged to a configured audit channel with minimal metadata only.

## Delivery and diagnostics

Permission and readiness checks are centralized so setup, previews, health checks, and live delivery share the same blockers and wording.

Diagnostics cover:

- missing `View Channel`
- missing `Send Messages`
- missing `Embed Links`
- missing `Manage Roles`
- deleted channels or roles
- role hierarchy issues
- invalid or ambiguous media configuration
- blocked saved Studio/admin content
- eligibility exclusions
- reaction-objective fallback when a live birthday announcement post never became available

Discord 400 invalid-payload failures are classified as permanent operator/config problems instead of retryable transport failures.

## Runtime health model

Runtime state is tracked explicitly in `RuntimeStatus` and exposed through the built-in HTTP server.

Tracked phases include:

- process start
- DB pool ready
- migrations start / complete / fail
- health server start / fail
- bot login start
- bot ready
- scheduler recovery start / complete / fail
- unexpected shutdown

HTTP endpoints:

- `/livez` for basic liveness
- `/readyz` for readiness
- `/healthz` and `/health` for detailed JSON state

`/readyz` is the canonical uptime-monitor endpoint.

## Reliability choices

- Celebration events are persisted before Discord side effects run.
- Event states are explicit: `pending`, `processing`, `completed`.
- Birthday Capsules, Quests, Surprises, and Timeline rows piggyback on the existing scheduler/event pipeline instead of introducing another worker.
- Reaction quest tracking uses the shared announcement message id plus a bounded in-memory debounce/cache instead of per-reactor tables or a background service.
- Permanent invalid payload/media failures complete as skipped instead of looping forever.
- Active role cleanup uses stored role snapshot data so config changes do not orphan roles.
- DM failures are recorded as skip outcomes without noisy retries.
- Stale-send recovery is bounded to 3 history requests of 10 bot-authored messages each.

## Privacy choices

- Birthdays are stored per guild membership, never as a cross-server profile.
- Birth year is optional and hidden by default.
- Admin setup, Studio, preview, health, and import/export flows are ephemeral.
- Logs and diagnostics do not include raw birth dates, birth years, raw template text, or raw blocked payloads.
- Message Content intent is not used.
- Quest reactions record only celebration-level totals; individual reactors are not stored.

## Extension seams

- `AnnouncementStudioPresentation` can expand without turning into an arbitrary embed builder.
- `celebration_events.payload` can carry future metadata for new event styles.
- `birthday_celebrations` is the compact seam for future badges, retention surfaces, and low-cost analytics.
- `birthday_wishes` can later feed optional on-demand generated cards/cakes without storing binaries.
- `tracked_member_anniversaries` remains a clean seam for richer anniversary features later.
- `recurring_celebrations` can back future server milestone experiences.
- Future generated cards/cakes can reuse the existing direct-media validation flow after an external asset service returns a URL.
