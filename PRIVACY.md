# Privacy and Threat Notes

## Data minimization

- Birthdays are stored per guild membership context, not globally across servers.
- Only the minimum birthday data needed for bot function is stored:
  - month
  - day
  - optional year
  - optional timezone override
  - server-scoped visibility choice
- Birth year is optional and hidden by default.
- Join anniversaries are tracked only when the bot has a reliable join timestamp for that member through birthday/admin flows or explicit sync.
- No message content is collected or required.
- This bot does not implement activity or inactivity tracking in this pass.

## Visibility model

- `private`: the stored birthday remains visible to the member and admins, but is excluded from normal member browse commands.
- `server_visible`: the member may appear in normal birthday browse commands for that server.
- Visibility is server-scoped. Bdayblaze does not provide a global public birthday profile or cross-server sharing.
- Birth year is never shown in non-admin browse flows, even when the birthday itself is server-visible.

## Logging rules

- Do not log birth dates, birth years, or full timezone-linked birthday records.
- Do not log raw custom message or template bodies when they may contain pasted personal data.
- Store only non-sensitive skip/error codes in retry metadata or delivery status where practical.
- Prefer internal identifiers and aggregate diagnostics over human-readable personal data in logs.

## User expectations

- `/birthday view` only exposes the caller's own stored record unless an admin explicitly uses member-management commands.
- `/birthday remove` provides a direct deletion path for the caller's record in the current server.
- `/birthday today`, `/birthday next`, `/birthday upcoming`, `/birthday month`, `/birthday twins`, and `/birthday list` are guild-scoped and visibility-aware.
- `/birthday privacy` explains what is stored and why.
- `/birthday member ...`, `/birthday import`, `/birthday export`, setup, Celebration Studio, health, and dry-run tools are admin-only and private to the admin using them.
- `/birthday test-message` sends a private preview only and reports live-delivery readiness separately from preview success.

## Import and export

- Birthday CSV import/export is scoped to the current server only.
- Export includes only the fields needed for bot operation:
  - `user_id`
  - `month`
  - `day`
  - `birth_year`
  - `timezone_override`
  - `visibility`
- Export delivery should remain private to admins.
- Import uses strict schema validation, attachment size limits, row-numbered errors, and a preview-before-apply flow.
- Exported CSV files are personal data and should be handled like sensitive operator data.

## Safe customization

- Custom announcement text uses a strict placeholder whitelist.
- There is no eval, Jinja, arbitrary attribute access, or free-form JSON embed execution.
- Celebration Studio stores compact configuration values such as validated URLs, text fields, preset names, and accent colors.
- Celebration Studio keeps those values structured and compact. It does not store uploaded binaries, arbitrary JSON embed payloads, or free-form rendering logic.
- Uploaded binaries are not stored in the database.

## Threat model summary

### Assets

- Birthday records
- Guild configuration
- Tracked anniversary records
- Recurring celebration definitions
- Server anniversary configuration
- Celebration event queue
- Announcement batch state
- Bot token and database credentials

### Primary risks

- Over-collection of personal data
- Birthday visibility leaking outside intended server scope
- Duplicate or missed celebrations after restarts
- Misconfigured permissions or role hierarchy causing noisy failures
- Channel or role IDs becoming stale after deletion
- Sensitive values leaking through logs, diagnostics, or CSV handling

### Mitigations

- Least-privilege intents and permissions.
- Guild-scoped storage with privacy-first defaults.
- Optional birth year and server-scoped visibility controls.
- Durable event queue plus persisted announcement-batch state for restart recovery.
- Bounded stale-send recovery scans only bot-authored messages in a narrow time window for the exact batch footer token.
- Centralized permission diagnostics and health reporting for stale config, missing permissions, and hierarchy problems.
- Graceful handling when members, roles, channels, or DM access disappear.

## Operational guidance

- Restrict database access to the bot runtime and migration workflow.
- Use environment variables or a secret manager for credentials.
- Prefer a pooled Postgres connection string.
- Treat exported CSVs, backups, and database snapshots as sensitive personal data.
- Review admin permissions before granting access to import/export or member-management flows.

## Edge-case product decisions

- Leap-day birthdays celebrate on February 28 in non-leap years.
- If a member changes timezone during an active celebration, the active role-removal window is preserved and the new timezone applies to future occurrences.
- If a member leaves the server, announcements and DMs are skipped safely and active role cleanup is attempted when possible.
- If a crash happens after an announcement is sent but before sent state is persisted, Bdayblaze uses a small bounded recovery scan to try to detect the existing message. If that message is deleted or outside the bounded recovery window, one duplicate announcement can still happen.
