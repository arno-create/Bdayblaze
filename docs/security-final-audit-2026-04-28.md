# Bdayblaze Final Security, Stability, and Trust Audit

Date: 2026-04-28

## Executive Summary

This audit reviewed the whole bot runtime with emphasis on public HTTP routes, Top.gg webhooks, media probing, Discord interaction failure paths, dependency policy, logging, and operator documentation. The patch keeps the product behavior compatible while making unsafe inputs fail closed.

Primary references:

- OWASP SSRF Prevention Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html
- OWASP Input Validation Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html
- OWASP Logging Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
- OWASP Vulnerable Dependency Management Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Vulnerable_Dependency_Management_Cheat_Sheet.html
- Python `hmac.compare_digest`: https://docs.python.org/3/library/hmac.html
- Discord application command permissions: https://discord.com/developers/docs/interactions/application-commands
- aiohttp 3.13.4 release/advisory notes: https://github.com/aio-libs/aiohttp/releases/tag/v3.13.4

## Findings Fixed

### F-01: Media probe redirect/DNS SSRF exposure

Impact: An admin-only media probe could validate the first URL but still follow redirects or DNS resolution toward private/internal targets.

Fixed in `src/bdayblaze/services/media_validation_service.py`:

- disabled automatic redirects
- validates every redirect destination through the same media URL classifier
- checks DNS results before requests and rejects loopback, private, link-local, reserved, multicast, and unspecified addresses
- uses the same public-IP resolver inside aiohttp connection setup to reduce DNS rebinding risk
- preserves short timeouts, HEAD-first probing, and tiny ranged GET fallback

Regression coverage:

- redirects to `127.0.0.1` are rejected
- public-looking hosts that resolve to private IPs are rejected
- valid direct-media probes still pass

### F-02: Malformed Top.gg payloads could escape as server errors

Impact: Invalid user ids, invalid weights, or missing event ids could raise exceptions instead of returning a controlled 400 response.

Fixed in `src/bdayblaze/services/vote_service.py`:

- centralized strict positive-integer parsing
- requires non-empty V2 event ids before persistence
- maps malformed V2 and legacy webhook payloads to `invalid_payload`
- avoids accidental idempotency collisions from missing ids

Regression coverage:

- non-numeric V2 user id
- missing V2 event id
- invalid V2 weight
- non-numeric legacy user id

### F-03: Legacy Top.gg secret comparison was not constant-time

Impact: V2 webhooks already used HMAC comparison, but legacy authorization compared strings directly.

Fixed in `src/bdayblaze/services/vote_service.py`:

- legacy authorization now uses the shared `hmac_compare` helper backed by `hmac.compare_digest`
- V2 remains the recommended secure setup

Regression coverage:

- legacy webhook auth path proves the constant-time helper is used

### F-04: Built-in HTTP parser lacked full malformed/slow-client hardening

Impact: Header reads and body reads were less bounded than the public route surface should allow, and invalid `Content-Length` could become a 500.

Fixed in `src/bdayblaze/http_server.py`:

- request-line, header, and body read timeouts
- max body size, max header bytes, max header line size, and max header line count
- malformed content length maps to 400
- slow/incomplete reads map to 408 or 400
- `/readyz` remains the canonical Render health check

Regression coverage:

- invalid `Content-Length`
- oversized header line
- existing health readiness behavior

### F-05: Scheduled Top.gg reminder embed could crash at send time

Impact: The reminder embed passed an unsupported keyword to `BudgetedEmbed.set_footer`, causing scheduled DM delivery to fail.

Fixed in `src/bdayblaze/discord/ui/vote.py`:

- footer call now uses the shared helper contract correctly
- reminder embed construction is covered by a focused test

### F-06: Manual Top.gg refresh failures could become interaction failures

Impact: Top.gg timeout/token/provider errors from manual refresh could bubble out of the Discord button callback.

Fixed in `src/bdayblaze/services/vote_service.py`:

- Top.gg refresh uses a bounded aiohttp timeout
- provider/network validation errors become a private `unavailable` result with a clear note
- no raw token, response body, or webhook body is surfaced

### F-07: Static quality gates had drifted

Impact: The branch had passing tests, but lint and type gates were failing, hiding real runtime bugs.

Fixed across runtime/tests:

- restored `mypy` strict success for `src/bdayblaze`
- restored `ruff check .` success
- added a typed repository protocol for Top.gg receipt operations
- cleaned test helpers without changing product behavior

### F-08: Dependency floor allowed vulnerable aiohttp versions

Impact: The dependency range allowed aiohttp versions older than the current fixed release floor checked during the audit.

Fixed in `pyproject.toml`:

- raised aiohttp from `>=3.10,<4.0` to `>=3.13.4,<4.0`

## Privacy and Trust Notes

- `.env` was not inspected.
- Touched webhook and refresh paths do not log raw webhook bodies, secrets, tokens, or response bodies.
- Studio audit logs continue to exclude raw blocked content and blocked URLs.
- Additional gateway/studio operational logs now redact guild/channel identifiers where practical.
- Health diagnostics remain public because Render uses the built-in service directly; the payload contains runtime state, not secrets or raw user content.

## Residual Risks

- Media validation verifies URL shape, redirects, DNS safety, content type, and magic bytes. It does not perform image-content moderation or NSFW vision scanning.
- Top.gg and Discord are third-party dependencies; transient provider outages are handled as unavailable/retryable states, not eliminated.
- `/healthz` is intentionally detailed for operators. Keep external monitors on `/readyz`, and put the runtime behind the deployment platform's normal controls if detailed runtime state should not be public.
- The repo does not add a new dependency-audit tool to runtime dependencies. Use `pip-audit` or equivalent in CI/release review when network access is available.

## Verification

Latest verification on this branch before final handoff:

- focused hardening suite: `77 passed`
- full suite: `264 passed`
- `ruff check .`: passed
- `mypy src\bdayblaze`: passed
- `compileall src tests`: passed
- dependency audit tool check: `pip_audit` is not installed in the local virtualenv

The remaining warning is from `discord.py` importing Python's deprecated `audioop` module under Python 3.12; the project already constrains Python to `<3.14`.
