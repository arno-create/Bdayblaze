# Bdayblaze

Bdayblaze is a production-minded Discord birthday bot foundation focused on reliable celebrations, privacy-first storage, and clean extension points for future premium features.

## MVP highlights

- Guild-scoped birthday storage with optional birth year and timezone override.
- Slash-first birthday commands with ephemeral admin setup, message-theme presets, and private operator preview tooling.
- Restart-safe scheduler with persisted next-occurrence fields, durable announcement batches, and event idempotency.
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

### Render

- Deploy this bot as a `Web Service`.
- Recommended Python runtime: `3.12.x`.
- Build command:

  ```bash
  pip install -e .
  ```

- Start command:

  ```bash
  python -m bdayblaze.main run
  ```

- Required environment variables:
  - `DISCORD_TOKEN`
  - `DATABASE_URL`
- Recommended environment variables:
  - `BDAYBLAZE_AUTO_RUN_MIGRATIONS=true` for the first deploy so the schema is created automatically
  - `BDAYBLAZE_LOG_LEVEL=INFO`
  - `PORT` is injected by Render automatically; do not set it yourself

The bot now starts a tiny built-in HTTP health server when `PORT` is present so Render port detection succeeds without changing Discord bot behavior. The repository includes `render.yaml` and `runtime.txt` so Render can pick the correct web-service shape and Python version.

## Commands

- `/help`
- `/about`
- `/birthday help`
- `/birthday about`
- `/birthday set`
- `/birthday view`
- `/birthday remove`
- `/birthday today`
- `/birthday next`
- `/birthday month`
- `/birthday twins`
- `/birthday upcoming`
- `/birthday setup`
- `/birthday message`
- `/birthday test-message`
- `/birthday list`
- `/birthday member view`
- `/birthday member set`
- `/birthday member remove`
- `/birthday health`
- `/birthday privacy`

## Testing and checks

```bash
pytest
ruff check .
mypy src
```

## Schema notes

- Migration `003_announcement_themes_and_birth_month_index.sql` adds the compact `announcement_theme` guild setting and an index on `(guild_id, birth_month, birth_day)` for month, twins, and currently-active birthday lookups.

## Product decisions worth noting

- Leap-day birthdays celebrate on February 28 during non-leap years.
- Announcements are scheduled from the member's effective timezone, not a global guild midnight.
- Birthday announcement text is customizable through a strict placeholder whitelist; reliable user mentions are still system-generated outside the template body.
- Literal braces can be escaped in templates with `{{` and `}}`.
- Announcement presentation stays compact: celebration mode controls the overall energy level, while a small set of presets controls title, color, emoji flavor, and footer styling.
- Mid-cycle config changes apply to future celebrations; active role-removal uses the role snapshot captured when the celebration started.
- Birthdays remain server-scoped in this version. Cross-server public birthday visibility is intentionally not enabled yet.
- `/birthday test-message` always sends a private preview and separately reports whether live delivery is actually ready in the current server.
- Stale-send recovery only scans Discord history for stale `sending` batches, using a hard cap of 3 requests x 10 bot-authored messages. If the original sent message is deleted or falls outside that bounded window before recovery runs, one duplicate announcement can still occur.
