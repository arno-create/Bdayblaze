# Bdayblaze

Bdayblaze is a production-minded Discord birthday bot foundation focused on reliable celebrations, privacy-first storage, and clean extension points for future premium features.

## MVP highlights

- Guild-scoped birthday storage with optional birth year and timezone override.
- Slash-first admin configuration with ephemeral setup UX.
- Restart-safe scheduler with persisted next-occurrence fields and celebration event idempotency.
- Health diagnostics for missing config, permissions, hierarchy issues, and scheduler lag.
- SQL migration workflow with direct async Postgres access.

## Tech stack

- Python 3.12+
- `discord.py` 2.x
- `asyncpg`
- PostgreSQL / Supabase

## Project layout

```text
src/bdayblaze/
  db/              Database pool and migration runner
  discord/         Cogs and interactive setup views
  domain/          Pure birthday/date logic and typed models
  repositories/    Thin async Postgres query layer
  services/        Application services and scheduler
```

## Local setup

1. Create a virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -e .[dev]
   ```

2. Copy `.env.example` to `.env` and fill in:

   - `DISCORD_TOKEN`
   - `DATABASE_URL`

3. Run migrations:

   ```bash
   python -m bdayblaze.main migrate
   ```

4. Start the bot:

   ```bash
   python -m bdayblaze.main run
   ```

## Discord application setup

- Enable the `applications.commands` scope.
- Bot permissions should stay minimal:
  - `View Channels`
  - `Send Messages`
  - `Embed Links`
  - `Manage Roles` only if birthday roles are enabled
- Do not enable the Message Content intent.
- Privileged intents are not required for the MVP. Member lookups use direct REST fetches when needed.

## Deployment notes

- Run a single scheduler instance unless you have verified the database-backed event claims across multiple workers in your environment.
- Keep `BDAYBLAZE_RECOVERY_GRACE_HOURS` comfortably above your worst-case deploy downtime.
- For Supabase, use a pooled connection string and keep the batch size modest on constrained hosts.
- The bot can optionally auto-run migrations on startup via `BDAYBLAZE_AUTO_RUN_MIGRATIONS=true`, but a separate migration step is safer for production.

## Commands

- `/birthday set`
- `/birthday view`
- `/birthday remove`
- `/birthday upcoming`
- `/bdayblaze setup`
- `/bdayblaze config`
- `/bdayblaze health`
- `/bdayblaze privacy`

## Testing and checks

```bash
pytest
ruff check .
mypy src
```

## Product decisions worth noting

- Leap-day birthdays celebrate on February 28 during non-leap years.
- Announcements are scheduled from the member's effective timezone, not a global guild midnight.
- Mid-cycle config changes apply to future celebrations; active role-removal uses the role snapshot captured when the celebration started.
