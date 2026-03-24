# Privacy and Threat Notes

## Data minimization

- Birthdays are stored per guild membership context, not globally across servers.
- Only the minimum required fields are stored:
  - month
  - day
  - optional year
  - optional timezone override
- Age is hidden by default.
- No message content is collected or required.

## Logging rules

- Do not log birth dates, birth years, or full timezone-linked birthday records.
- Store only redacted error codes in celebration event retry metadata.
- Prefer hashed identifiers for operational correlation where needed.

## User expectations

- `/birthday view` only exposes the caller's own stored record.
- `/birthday remove` provides a direct deletion path.
- `/birthday upcoming` is guild-scoped and intentionally omits year and age.
- `/bdayblaze privacy` explains what is stored and why.

## Threat model summary

### Assets

- Birthday records
- Guild configuration
- Celebration event queue
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
- Durable event queue with restart recovery.
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
