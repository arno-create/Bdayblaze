# Bdayblaze Privacy Policy

Bdayblaze is a Discord birthday bot built around server-scoped storage, private admin tooling, and low-noise celebration features. This document explains what the bot stores and how that data is used.

The public website version of this policy lives at [https://arno-create.github.io/Bdayblaze/privacy/](https://arno-create.github.io/Bdayblaze/privacy/).

## What Bdayblaze stores

For each saved birthday record, Bdayblaze stores:

- birthday month
- birthday day
- optional birth year
- optional timezone override
- server-scoped visibility choice

If a server uses other product surfaces, Bdayblaze may also store:

- guild settings for birthday, anniversary, and Studio behavior
- Birthday Capsule wishes for the current server
- compact Birthday Quest progress tied to an active celebration
- compact Timeline history and surprise reward state
- tracked anniversary and recurring event configuration
- scheduler and delivery state required to run the bot reliably

## Server scope and visibility

- Birthday data is stored per guild membership, not as a global cross-server profile.
- Visibility is server-scoped.
- Visibility modes are:
  - `private`: visible to the member and admins only
  - `server_visible`: visible in normal birthday browse commands for that server
- Birth year is optional and hidden by default in non-admin browse flows.

## Birthday Capsules, Quests, Timeline, and Surprises

- Birthday Capsule wishes are stored per guild and stay private until the target member's birthday window opens.
- Birthday Quests can use wish progress, optional check-ins, and reactions on the shared birthday announcement post.
- Reaction quests store celebration-level totals tied to the announcement message. They do not store individual reactor identities.
- Timeline history stores compact celebration metadata such as counts, quest completion, and reward state.
- Birthday Surprises remain compact reward records.
- Nitro concierge is manual admin follow-up only. Bdayblaze does not buy, gift, or deliver Nitro.

## Admin surfaces and privacy

The following flows are designed to stay private to the admin using them:

- `/birthdayadmin studio`
- `/birthdayadmin setup`
- `/birthdayadmin test-message`
- `/birthdayadmin analytics`
- `/birthdayadmin health`
- `/birthdayadmin import`
- `/birthdayadmin export`
- `/birthdayadmin member ...`

These flows are intended for operator control, previewing, diagnostics, and maintenance rather than public server output.

## Logging and safety

Bdayblaze is designed to avoid logging raw sensitive celebration content in normal operational logs. The bot is built to avoid logging:

- raw birth dates
- raw birth years
- raw Birthday Capsule wish text
- raw wish links
- raw blocked Studio or admin payloads
- raw blocked media URLs

Where possible, the bot prefers internal ids, status codes, field names, and compact diagnostics.

## Media validation

- Admin-supplied image URLs can be checked with short, bounded validation requests.
- The goal is to distinguish direct media from webpages, unsupported files, or unsafe links before save.
- The bot does not continuously fetch saved media in the background.
- The bot does not perform image-content moderation or NSFW vision scanning.

## Optional Studio audit logging

Servers can optionally enable a Studio safety audit channel.

When enabled, Bdayblaze logs only minimal metadata for blocked Studio or admin save attempts:

- actor
- surface
- field names
- blocked category
- timestamp

The audit log intentionally excludes raw blocked template text, raw blocked URLs, birth dates, and birth years.

## Import, export, and deletion

- CSV import and export are scoped to the current server only.
- Export delivery remains private to admins.
- Export includes only the fields required for bot operation:
  - `user_id`
  - `month`
  - `day`
  - `birth_year`
  - `timezone_override`
  - `visibility`
- Members can delete their own record with `/birthday remove`.
- Admins can remove records privately with `/birthdayadmin member remove`.
- Exported CSV files should be treated as personal data by the server administrators who request them.

## What Bdayblaze does not do

- It does not create a global birthday identity across servers.
- It does not use Message Content intent for the release surface documented in this repository.
- It does not store per-reactor identity for reaction quests.
- It does not run an automated Nitro purchase or gifting workflow.
- It does not include AI-generated birthday cards or cakes in this release surface.

## Contact and links

- Website: [Bdayblaze](https://arno-create.github.io/Bdayblaze/)
- Repository: [GitHub](https://github.com/arno-create/Bdayblaze)
- Support server: [Discord support server](https://discord.com/servers/inevitable-friendship-1322933864360050688)
