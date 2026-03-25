# Bdayblaze

Bdayblaze is a production-minded Discord birthday bot focused on privacy-first storage, low-noise admin UX, bounded recovery work, and release-grade operator tooling.

## Highlights

- Guild-scoped birthday storage with optional birth year, per-user timezone override, and server-scoped visibility.
- Slash-first UX with private `/birthday studio`, `/birthday setup`, and `/birthday test-message` flows.
- Birthday Capsules, Birthday Quests, Birthday Surprises, and `/birthday timeline` profiles without extra workers.
- Quest reactions can use the shared birthday announcement post without Message Content intent or per-reactor storage.
- Restart-safe scheduler with durable event records, bounded stale-send recovery, and no privileged intents.
- Celebration Studio with strict placeholder validation, guided media tools, compact previews, and explicit reset paths.
- Deterministic abuse protection for saved Studio/admin text plus practical unsafe-URL blocking.
- Built-in health endpoints for Render-style hosting with separate liveness and readiness signals.

## Tech stack

- Python 3.12+
- `discord.py` 2.x
- `asyncpg`
- `aiohttp`
- PostgreSQL / Supabase

## Project layout

```text
src/bdayblaze/
  db/              Database pool and migration runner
  discord/         Cogs, gateway, embeds, and interactive admin views
  domain/          Pure date logic, media/template validation, themes, and typed models
  repositories/    Thin async Postgres query layer
  services/        Birthday flows, settings, scheduler, diagnostics, and health
```

## Local setup

1. Create a virtual environment and install dependencies.

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -e .[dev]
   ```

2. Copy `.env.example` to `.env` and set:
   - `DISCORD_TOKEN`
   - `DATABASE_URL`

3. Run migrations.

   ```bash
   python -m bdayblaze.main migrate
   ```

4. Start the bot.

   ```bash
   python -m bdayblaze.main run
   ```

## Discord application setup

- Enable the `applications.commands` scope.
- Required bot permissions:
  - `View Channels`
  - `Send Messages`
  - `Embed Links`
- `Manage Roles` is only needed if birthday roles are enabled.
- Do not enable Message Content intent.
- Privileged intents are not required.
- Birthday Quest reaction tracking uses only the non-privileged guild reaction intent already enabled in code.

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
- `/birthday timeline`
- `/birthday wish add`
- `/birthday wish list`
- `/birthday wish remove`
- `/birthday capsule preview`
- `/birthday quest status`
- `/birthday quest check-in`
- `/birthday list`
- `/birthday privacy`

### Admin commands

- `/birthday studio`
- `/birthday setup`
- `/birthday test-message`
- `/birthday analytics`
- `/birthday surprise queue`
- `/birthday surprise fulfill`
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

## Celebration Studio

`/birthday studio` is the canonical admin customization flow.

Studio covers:

- birthday announcements
- birthday DMs
- member anniversaries
- server anniversary
- Birthday Capsules
- Birthday Quests
- Birthday Surprises
- yearly recurring events overview

### Media Tools

Use `/birthday studio` -> `Media tools` for shared image and thumbnail URLs.

Accepted states:

- `Direct media accepted`: Discord should usually render it as an embed image.
- `Webpage link rejected`: the URL resolves to a page, not a direct image/GIF/WebP asset.
- `Unsupported media rejected`: the URL points to a file type Discord will not render as embed media.
- `Needs validation`: the URL looks safe but must be probed before saving.
- `Invalid or unsafe URL rejected`: the URL is blocked locally.
- `Validation unavailable`: the probe could not confirm the URL right now.

Direct-media examples:

- `https://cdn.example.com/birthday/banner.gif`
- `https://images.example.com/render?id=42&sig=abc123`
- `https://storage.example.com/assets/celebration`

Webpage example:

- `https://www.example.com/gallery/photo-42`

Important behavior:

- Studio save-time media validation is stricter than a plain `https://` check.
- Failed saves never clear the last saved media entry.
- Query-string and signed URLs are supported.
- Extensionless object-storage URLs are supported only through Media Tools validation.
- HTML pages are not treated as image assets.
- Shared media is never used for live birthday DMs.
- `Reset media` clears only the shared image and thumbnail fields.

### Birthday Capsules, Quests, and Surprises

- Birthday Capsules stay private until the target member's birthday window opens.
- Wishes are text-first and can include one optional safe HTTPS link.
- Birthday Quests can track:
  - revealed wish count
  - reactions on the shared birthday announcement post when a live public route exists
  - optional birthday check-in
- Reaction quests use the shared post total reaction count across emoji and do not store individual reactors.
- If no live public birthday post exists, the reaction objective is skipped instead of blocking quest completion.
- Birthday Surprises stay compact and server-safe:
  - `featured`
  - `badge`
  - `custom_note`
  - `nitro_concierge`
- Nitro concierge is always manual admin follow-up. The bot never purchases, gifts, or delivers Nitro.
- `/birthday timeline` is the member-facing celebration card for countdowns, active quest progress, capsule state, and recent celebration history.

### Preview and safety

- `/birthday test-message` stays the canonical dry-run command.
- Studio previews and `/birthday test-message` are private and never ping members.
- Preview is the final Discord render check.
- Studio blocks obvious profanity, NSFW wording, slurs, harassment-style language, and unsafe URL patterns.
- The bot does not perform image-content moderation or NSFW vision scanning.

### Optional audit logging

`/birthday setup` now includes a `Studio safety` panel.

- You can set an optional Studio audit channel for blocked Studio/admin save attempts.
- Audit logs are minimal: actor, surface, field names, blocked category, and timestamp.
- Raw blocked template text, raw blocked media URLs, birth dates, and birth years are not logged.
- Audit logging is off by default.

## Privacy and safety model

- Birthday records are stored per guild membership, never globally across servers.
- Birth year is optional and hidden by default.
- Birthday Capsule wishes are stored per guild, revealed on the birthday, and not logged raw.
- Reaction quests store only per-celebration reaction totals tied to the birthday announcement message id.
- Timeline history stores compact celebration metadata, not large text snapshots.
- Nitro concierge is manual admin fulfillment only; the bot never buys or sends Nitro.
- Visibility is server-scoped:
  - `private`: visible to the member and admins only
  - `server_visible`: visible in normal browse commands for that server
- Admin setup, Studio, preview, health, import/export, and member-management flows stay ephemeral.
- Template rendering uses a strict placeholder whitelist.
- There is no Jinja, eval, arbitrary attribute access, or free-form embed JSON.

## Deployment notes

- Run a single scheduler instance unless you have independently validated your multi-worker setup.
- Keep `BDAYBLAZE_RECOVERY_GRACE_HOURS` above worst-case deploy downtime.
- Use a pooled Postgres connection string on Supabase.
- `BDAYBLAZE_AUTO_RUN_MIGRATIONS=true` is convenient for first deploys, but a separate migration step is safer in production.

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
  - `BDAYBLAZE_AUTO_RUN_MIGRATIONS=true`
  - `BDAYBLAZE_LOG_LEVEL=INFO`
  - `BDAYBLAZE_RECOVERY_GRACE_HOURS=36`
  - `BDAYBLAZE_SCHEDULER_MAX_SLEEP_SECONDS=300`
  - `BDAYBLAZE_SCHEDULER_BATCH_SIZE=25`

- `PORT` is provided by Render. Do not set it manually.
- `render.yaml` sets `healthCheckPath: /readyz`.

### Built-in health endpoints

- `/livez`
  - For process liveness only.
  - Returns `200` when the process and event loop are up.

- `/readyz`
  - Recommended uptime-monitor target.
  - Returns `200` only when the bot is ready, scheduler recovery completed, and the scheduler heartbeat is fresh.
  - Returns `503` while starting or when runtime health is degraded.

- `/healthz` and `/health`
  - Detailed JSON for debugging and monitors that want phase/state data.
  - Includes startup phase, scheduler counters, last iteration time, and runtime phase timestamps.

Common failure causes to verify:

- missing or invalid `DISCORD_TOKEN`
- missing or invalid `DATABASE_URL`
- failed migrations
- failed health-server bind
- stale scheduler heartbeat after a deploy/runtime issue
- missing channel or role permissions
- wrong Render linked branch or disabled auto-deploy in the Render dashboard

The code can expose clearer startup state, but it cannot force Render to auto-deploy the correct branch. Verify that in Render itself.

## Reliability notes

- Leap-day birthdays celebrate on February 28 in non-leap years.
- Announcements are scheduled from each member's effective timezone.
- Scheduler state is persisted before Discord side effects run.
- Stale-send recovery is bounded to 3 history requests of 10 bot-authored messages each.
- If a sent message is deleted or falls outside that bounded recovery window before recovery runs, one duplicate announcement can still occur.
- Reaction quest refreshes are debounced by announcement message id to keep API and database churn bounded on busy servers.

## Schema notes

- Migration `003_announcement_themes_and_birth_month_index.sql` adds theme presets and a browse index on `(guild_id, birth_month, birth_day)`.
- Migration `004_operator_ready_pass.sql` adds:
  - visibility controls
  - Celebration Studio presentation fields
  - birthday DM and anniversary settings
  - large-server eligibility controls
  - tracked member anniversaries
  - recurring annual celebrations
- Migration `005_server_anniversary_kind.sql` adds first-class server-anniversary metadata on recurring celebrations.
- Migration `006_studio_audit_channel_and_runtime_notes.sql` adds nullable `studio_audit_channel_id` to `guild_settings`.
- Migration `007_signature_feature_wave.sql` adds:
  - `guild_experience_settings`
  - `birthday_wishes`
  - `birthday_celebrations`
  - `guild_surprise_rewards`
  - `capsule_reveal` scheduler events
- Migration `008_reaction_quest_tracking.sql` adds:
  - `quest_reaction_target` on `guild_experience_settings`
  - `announcement_message_id` on `birthday_celebrations`
  - reaction quest counters and goal state on `birthday_celebrations`
  - an index for tracked birthday announcement message lookups

## Website and legal

- `website/` contains the repo-managed static landing page bundle with no build step.
- `LICENSE` ships Apache 2.0 for the project.
- `NOTICE` keeps project attribution alongside the license.
- AI-generated cake/card rendering is intentionally not part of this release candidate yet.

## Testing and checks

```bash
pytest
ruff check .
mypy src
python -m compileall src tests
```
