# Privacy and Threat Notes

## Data minimization

- Birthdays are stored per guild membership, not globally across servers.
- Stored birthday data is limited to:
  - month
  - day
  - optional year
  - optional timezone override
  - server-scoped visibility choice
- Join anniversaries are tracked only when the bot has a reliable join timestamp through birthday/admin flows or explicit anniversary sync.
- No message content is collected.
- No activity or inactivity tracking is performed in this pass.

## Visibility model

- `private`: visible to the member and admins only.
- `server_visible`: visible in standard birthday browse commands for that server.
- Visibility is server-scoped.
- Birth year is never shown in non-admin browse flows.

## Logging rules

- Do not log raw birth dates or birth years.
- Do not log raw custom message bodies or templates.
- Do not log raw blocked Studio/admin payloads.
- Prefer internal IDs, status codes, and aggregate diagnostics over sensitive content.

## Studio and customization safety

- Celebration Studio uses a strict placeholder whitelist.
- There is no eval, Jinja, arbitrary attribute access, or raw embed JSON execution.
- Saved Studio data stays compact: text fields, preset names, colors, channel IDs, and validated URLs.
- Uploaded binaries are not stored in the database.

### Media handling

- Media URLs must use HTTPS and a public host.
- The bot distinguishes direct-media URLs from normal webpages.
- Signed URLs, query-string URLs, and extensionless object-storage URLs may be accepted only when Media Tools validation succeeds.
- Admin-triggered media validation uses a short, bounded outbound probe.
- The bot does not continuously fetch saved media in the background.
- The bot does not perform image-content moderation.

### Abuse protection scope

Studio/admin save flows block obvious:

- profanity or vulgarity
- sexual / NSFW wording
- hateful slurs
- harassment-style threat wording
- unsafe URL keywords and unsafe host patterns

This is a deterministic rules layer, not full moderation. It is meant to catch obvious misuse, not replace human moderation or image scanning.

## Optional Studio audit logging

- Admins can optionally configure a Studio audit channel from `/birthday setup` -> `Studio safety`.
- Audit logging is off by default.
- When enabled, blocked Studio/admin attempts log only:
  - actor
  - surface
  - field names
  - blocked category
  - timestamp
- Raw blocked template text, raw blocked URLs, birth dates, and birth years are intentionally excluded.
- Repeated identical blocked attempts are deduped for a short in-memory window to avoid spam.

## User expectations

- `/birthday view` exposes only the caller's own stored record unless an admin uses member-management commands.
- `/birthday remove` deletes the caller's record for the current server.
- `/birthday today`, `/birthday next`, `/birthday upcoming`, `/birthday month`, `/birthday twins`, and `/birthday list` remain guild-scoped and visibility-aware.
- `/birthday member ...`, `/birthday import`, `/birthday export`, `/birthday setup`, `/birthday studio`, `/birthday health`, and `/birthday test-message` are admin-oriented and private to the admin using them.
- `/birthday test-message` is preview-only and never posts a live celebration.

## Import and export

- Birthday CSV import/export is scoped to the current server only.
- Export includes only fields required for bot operation:
  - `user_id`
  - `month`
  - `day`
  - `birth_year`
  - `timezone_override`
  - `visibility`
- Export delivery remains private to admins.
- Import uses schema validation, size limits, row-numbered errors, and a preview-before-apply flow.
- Exported CSV files should be treated as personal data.

## Threat summary

### Assets

- Birthday records
- Guild configuration
- Tracked anniversary records
- Recurring celebration definitions
- Server-anniversary configuration
- Celebration event queue
- Announcement batch state
- Bot token and database credentials

### Primary risks

- over-collection of personal data
- birthday visibility leaking outside intended guild scope
- duplicate or missed celebrations after restarts
- stale channels or roles causing noisy failures
- sensitive content leaking through logs, diagnostics, or exports

### Mitigations

- least-privilege intents and permissions
- guild-scoped storage with privacy-first defaults
- optional birth year and server-scoped visibility
- durable event queue and persisted batch state for recovery
- bounded stale-send recovery
- centralized permission diagnostics and health reporting
- minimal audit logging for blocked Studio/admin abuse attempts

## Operational guidance

- Restrict database access to the bot runtime and migration workflow.
- Use environment variables or a secret manager for credentials.
- Prefer pooled Postgres connections.
- Treat exports, backups, and database snapshots as sensitive personal data.
- Review who has admin access to import/export, member-management, and Studio flows.

## Edge-case product decisions

- Leap-day birthdays celebrate on February 28 in non-leap years.
- If a member changes timezone during an active celebration, role-removal timing is preserved and the new timezone applies to future occurrences.
- If a member leaves the server, announcements and DMs are skipped safely and role cleanup is attempted when possible.
- If a crash happens after a send but before send state is persisted, the bot performs only a small bounded recovery scan. If the original message is gone or outside that window, one duplicate announcement can still occur.
