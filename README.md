# Bdayblaze

Bdayblaze is a production-minded Discord birthday bot focused on reliable celebrations, privacy-first storage, low-noise operator tooling, and clean extension points for later premium features.

## Highlights

- Guild-scoped birthday storage with optional birth year, per-user timezone override, and server-scoped visibility controls.
- Slash-first UX with top-level `/help` and `/about`, compact `/birthday` subcommands, ephemeral setup flows, and private dry-run previews.
- Restart-safe scheduler with persisted next-occurrence timestamps, durable event records, bounded stale-send recovery, and no Message Content intent.
- Rich but compact Celebration Studio customization with strict placeholder validation, safe embed media settings, and bounded Discord-safe admin panels.
- Practical operator tooling: permission diagnostics, health checks, admin member CRUD, CSV import/export, tracked join anniversaries, and annual recurring celebrations.
- Large-server controls that stay cheap to run: eligibility role, ignore bots, minimum membership age, and mention suppression on large batches.

## Tech stack

- Python 3.12+
- `discord.py` 2.x
- `asyncpg`
- PostgreSQL / Supabase

## Project layout

```text
src/bdayblaze/
  db/              Database pool and migration runner
  discord/         Cogs, gateway, and interactive setup/message views
  domain/          Pure birthday/date logic, templates, themes, and typed models
  repositories/    Thin async Postgres query layer
  services/        Birthday flows, settings, health, diagnostics, and scheduler
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
- Privileged intents are not required. Member resolution uses direct fetches and bounded lookups when needed.

## Deployment notes

- Run a single scheduler instance unless you have validated database-backed event claims in your own multi-worker environment.
- Keep `BDAYBLAZE_RECOVERY_GRACE_HOURS` comfortably above worst-case deploy downtime.
- For Supabase, use a pooled connection string and keep scheduler batch sizes modest on constrained hosts.
- `BDAYBLAZE_AUTO_RUN_MIGRATIONS=true` can bootstrap schema automatically, but a separate migration step is safer for production.

### Render

- Deploy as a `Web Service`.
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
  - `BDAYBLAZE_AUTO_RUN_MIGRATIONS=true` for the first deploy
  - `BDAYBLAZE_LOG_LEVEL=INFO`
  - `PORT` is injected by Render automatically; do not set it yourself

When `PORT` is present, the bot starts a small built-in HTTP health server so Render port detection succeeds without changing Discord bot behavior. The repository includes `render.yaml` and `runtime.txt` for Render-friendly defaults.

## Command surface

### Top-level

- `/help`
- `/about`

### Member commands

- `/birthday set`
- `/birthday view`
- `/birthday remove`
- `/birthday today`
- `/birthday next`
- `/birthday upcoming`
- `/birthday month`
- `/birthday twins`
- `/birthday list`
- `/birthday privacy`

### Admin commands

- `/birthday setup`
- `/birthday message`
- `/birthday test-message`
- `/birthday export`
- `/birthday import`
- `/birthday member view`
- `/birthday member set`
- `/birthday member remove`
- `/birthday anniversary settings`
- `/birthday anniversary sync`
- `/birthday event add`
- `/birthday event edit`
- `/birthday event list`
- `/birthday event remove`
- `/birthday health`

## Product behavior

### Privacy model

- Birthday records are stored per server membership, never globally across servers.
- Birth year is optional and hidden by default.
- Visibility is server-scoped:
  - `private`: only the member and admins see the stored profile in browse/manage flows.
  - `server_visible`: the member can appear in normal server browse commands.
- Admin and preview workflows stay ephemeral by default.
- Export/import is admin-only and should be treated as personal data handling.

### Browsing and discovery

- `/birthday today` reflects members currently celebrating according to the scheduler's timezone-aware celebration window, not a simple guild-midnight list.
- `/birthday next`, `/birthday upcoming`, `/birthday month`, `/birthday twins`, and `/birthday list` respect visibility settings for non-admin flows.
- Admin browse flows can opt into private entries where appropriate.
- Output stays bounded and mobile-friendly; large public dumps are intentionally avoided.

### Celebration Studio

- `/birthday message` opens Celebration Studio, the private admin surface for:
  - birthday announcements
  - birthday DMs
  - member anniversaries
  - server anniversary
  - custom annual event overview
- The studio keeps announcement presentation compact and safe:
  - theme preset
  - optional title override
  - description template
  - optional footer text
  - optional image URL
  - optional thumbnail URL
  - optional accent color override
- Theme presets include `classic`, `festive`, `minimal`, `cute`, `elegant`, and `gaming`.
- Template rendering uses a strict placeholder whitelist. There is no Jinja, eval, or arbitrary embed builder.
- Literal braces can be escaped with `{{` and `}}`.
- Long templates, placeholder references, and diagnostics are chunked or trimmed inside the panel so Discord embed limits are not exceeded.
- Studio modal saves return a compact confirmation card with a path back to the relevant studio section instead of spawning another full-size panel.

### Event coverage

- Birthday announcements remain the primary event type.
- Optional birthday DM greetings can be enabled per server.
- Join anniversaries are supported as tracked annual announcements. This pass keeps them tracked-only rather than full-guild auto-discovery.
- Server anniversary is a first-class annual celebration. It defaults to the guild creation date when Discord provides it, and admins can override that date privately.
- Admins can also define compact annual recurring celebrations such as a community founding day.

### Large-server controls

- `eligibility_role_id`: only members with the configured role qualify.
- `ignore_bots`: enabled by default.
- `minimum_membership_days`: blocks celebrations for very new members when configured.
- `mention_suppression_threshold`: avoids noisy large mention bursts in big batches.
- There is no activity-based eligibility in this pass. Bdayblaze does not track message content or invent low-confidence inactivity heuristics.

### Reliability and recovery

- Leap-day birthdays celebrate on February 28 in non-leap years.
- Announcements are scheduled from the member's effective timezone, not a global guild midnight.
- Scheduler state is persisted before Discord side effects run.
- `/birthday test-message` renders a private preview and separately reports live readiness.
- Dry-run previews exist for birthday announcements, birthday DMs, member anniversaries, server anniversary, and recurring annual events.
- Stale-send recovery only scans Discord history for stale `sending` batches, capped at 3 requests of 10 bot-authored messages each.
- If an original sent message is deleted or falls outside that bounded recovery window before recovery runs, one duplicate announcement can still occur.
- Late recovered announcements can use graceful wording without weakening dedupe guarantees.

## Schema notes

- Migration `003_announcement_themes_and_birth_month_index.sql` adds compact theme presets and an index on `(guild_id, birth_month, birth_day)`.
- Migration `004_operator_ready_pass.sql` adds:
  - birthday visibility controls
  - Celebration Studio presentation fields
  - birthday DM and anniversary settings
  - large-server eligibility controls
  - tracked member anniversaries
  - recurring annual celebrations
  - additional scheduler event kinds and browse/scheduler indexes
- Migration `005_server_anniversary_kind.sql` adds recurring-celebration metadata so server anniversary can be stored as an explicit first-class annual event while still using the existing recurring scheduler path.

## Testing and checks

```bash
pytest
ruff check .
mypy src
python -m compileall src tests
```
