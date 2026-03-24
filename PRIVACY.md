# Privacy and Threat Notes

## Data minimization

- Birthdays are stored per guild membership context, not globally across servers.
- Only the minimum required fields are stored:
  - month
  - day
  - optional year
  - optional timezone override
- Age is not shown publicly and there is no cross-server birthday visibility feature in this version.
- No message content is collected or required.

## Logging rules

- Do not log birth dates, birth years, or full timezone-linked birthday records.
- Do not log raw announcement-template content when it may contain personal data.
- Store only redacted error codes in celebration event retry metadata.
- Prefer hashed identifiers for operational correlation where needed.

## User expectations

- `/birthday view` only exposes the caller's own stored record.
- `/birthday remove` provides a direct deletion path.
- `/birthday upcoming`, `/birthday today`, `/birthday next`, `/birthday month`, and `/birthday twins` are guild-scoped and intentionally omit year and age.
- `/birthday privacy` explains what is stored and why.
- `/birthday list` and `/birthday member ...` are admin-only and private to the admin using them.
- `/birthday message` is admin-only and edits the announcement body only; reliable user mentions remain system-generated outside the custom template.
- `/birthday test-message` sends a private preview only and reports live-delivery readiness separately from preview success.

## Threat model summary

### Assets

- Birthday records
- Guild configuration
- Celebration event queue
- Announcement batch state
- Bot token and database credentials

### Primary risks

- Over-collection of personal data
- Duplicate or missed celebrations after restarts
- Misconfigured role hierarchy causing noisy failures
- Channel or role IDs becoming stale after deletion
- Sensitive values leaking through logs or diagnostics

### Mitigations in the MVP

- Least-privilege intents and permissions.
- Guild-scoped storage and optional birth year.
- Durable event queue plus persisted announcement-batch state for restart recovery.
- Bounded stale-send recovery scans only bot-authored messages in a narrow time window for the exact batch footer token.
- Health command for stale config, permissions, and scheduler lag.
- Graceful handling when members, roles, or channels disappear.

## Operational guidance

- Restrict database access to the bot runtime and migration workflow.
- Use environment variables or secret management for credentials.
- Prefer a pooled Postgres connection string.
- Treat exported database snapshots as sensitive personal data.

## Edge-case product decisions

- Leap-day birthdays celebrate on February 28 in non-leap years.
- If a member changes timezone during an active celebration, the active role-removal window is preserved and the new timezone applies to future occurrences.
- If a member leaves the server, celebrations are skipped safely and active role cleanup is attempted if the member returns before the removal event expires.
- If a crash happens after an announcement is sent but before sent state is persisted, Bdayblaze uses a small bounded recovery scan to try to detect the existing message. If that original message is gone or outside the bounded recovery window, one duplicate announcement can still happen.
