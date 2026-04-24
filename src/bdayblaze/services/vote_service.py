from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import aiohttp

from bdayblaze.config import Settings
from bdayblaze.domain.topgg import (
    TopggVoteReceipt,
    TopggVoteReminder,
    TopggWebhookMode,
    TopggWebhookResult,
    VoteBonusStatus,
    VoteRefreshResult,
    VoteReminderUpdateResult,
    build_v2_signature,
    is_v2_webhook_secret,
    parse_signature_header,
    parse_topgg_datetime,
    payload_sha256,
)
from bdayblaze.services.errors import ValidationError


class VoteService:
    DEFAULT_WISH_CHARACTER_LIMIT = 350
    BONUS_WISH_CHARACTER_LIMIT = 500
    DEFAULT_TIMELINE_ENTRY_LIMIT = 6
    BONUS_TIMELINE_ENTRY_LIMIT = 12
    TOPGG_VOTE_URL_TEMPLATE = "https://top.gg/bot/{bot_id}/vote"
    LEGACY_VOTE_WINDOW = timedelta(hours=12)
    REMINDER_LEAD_TIME = timedelta(minutes=30)
    REMINDER_LAST_MINUTE_DELAY = timedelta(minutes=1)
    REMINDER_MINIMUM_REMAINING = timedelta(minutes=5)
    REMINDER_RETRY_DELAY = timedelta(minutes=5)
    REMINDER_MAX_ATTEMPTS = 3

    def __init__(
        self,
        repository: object,
        *,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._refresh_last_called_at: dict[int, datetime] = {}
        if settings.topgg_enabled:
            self._storage_ready = False
            self._storage_message = "Top.gg storage check is pending."
        else:
            self._storage_ready = True
            self._storage_message = "Top.gg storage is idle while the vote lane is disabled."

    @property
    def vote_url(self) -> str:
        return self.TOPGG_VOTE_URL_TEMPLATE.format(bot_id=self._settings.topgg_bot_id)

    async def initialize_storage_state(self) -> None:
        probe = getattr(self._repository, "probe_topgg_storage", None)
        if not callable(probe):
            self._storage_ready = False
            self._storage_message = "Top.gg storage probe is unavailable."
            return
        try:
            ready, message = await probe()
        except Exception:
            self._storage_ready = False
            self._storage_message = "Top.gg storage probe failed."
            return
        self._storage_ready = bool(ready)
        self._storage_message = str(message)

    def diagnostics_snapshot(self) -> dict[str, object]:
        enabled = self._settings.topgg_enabled
        secret = self._settings.topgg_webhook_secret.strip()
        webhook_mode = self._webhook_mode(secret)
        if not enabled:
            state = "disabled"
            message = "Top.gg vote bonus is disabled."
        elif not secret:
            state = "misconfigured"
            message = "Top.gg vote bonus is enabled but TOPGG_WEBHOOK_SECRET is missing."
        else:
            state = "ready"
            if self._settings.topgg_token:
                message = "Top.gg vote bonus is enabled and ready."
            else:
                message = (
                    "Top.gg vote bonus is enabled. Webhooks are active. "
                    "Optional manual refresh is not configured on this deployment."
                )
        refresh_available = enabled and state == "ready" and bool(self._settings.topgg_token)
        reminder_ready = (not enabled) or (state == "ready" and self._storage_ready)
        return {
            "enabled": enabled,
            "configuration_state": state,
            "configuration_message": message,
            "webhook_mode": webhook_mode,
            "storage_ready": self._storage_ready,
            "storage_backend": "postgres",
            "storage_message": self._storage_message,
            "refresh_available": refresh_available,
            "refresh_cooldown_seconds": self._settings.topgg_refresh_cooldown_seconds,
            "timing_source": (
                "exact"
                if webhook_mode == "v2"
                else "legacy_estimated"
                if webhook_mode == "legacy"
                else None
            ),
            "reminder_ready": reminder_ready,
            "reminder_delivery_mode": "dm",
        }

    async def get_vote_bonus_status(
        self,
        discord_user_id: int,
        *,
        now_utc: datetime | None = None,
    ) -> VoteBonusStatus:
        now = now_utc or datetime.now(UTC)
        diagnostics = self.diagnostics_snapshot()
        refresh_retry_after_seconds = self._refresh_retry_after_seconds(discord_user_id, now_utc=now)
        receipt = await self.fetch_latest_vote_receipt(discord_user_id)
        reminder = await self.fetch_vote_reminder(discord_user_id)
        if diagnostics["configuration_state"] == "disabled":
            return self._build_status(
                lane_state="disabled",
                active=False,
                voted_at_utc=receipt.vote_created_at if receipt is not None else None,
                expires_at_utc=receipt.vote_expires_at if receipt is not None else None,
                timing_source=receipt.timing_source if receipt is not None else None,
                weight=receipt.weight if receipt is not None else None,
                refresh_available=False,
                refresh_retry_after_seconds=None,
                configuration_message=str(diagnostics["configuration_message"]),
                reminder=reminder,
            )
        if diagnostics["configuration_state"] != "ready":
            return self._build_status(
                lane_state="misconfigured",
                active=False,
                voted_at_utc=receipt.vote_created_at if receipt is not None else None,
                expires_at_utc=receipt.vote_expires_at if receipt is not None else None,
                timing_source=receipt.timing_source if receipt is not None else None,
                weight=receipt.weight if receipt is not None else None,
                refresh_available=False,
                refresh_retry_after_seconds=None,
                configuration_message=str(diagnostics["configuration_message"]),
                reminder=reminder,
            )
        if receipt is None or receipt.vote_expires_at is None or receipt.vote_expires_at <= now:
            return self._build_status(
                lane_state="inactive",
                active=False,
                voted_at_utc=receipt.vote_created_at if receipt is not None else None,
                expires_at_utc=receipt.vote_expires_at if receipt is not None else None,
                timing_source=receipt.timing_source if receipt is not None else None,
                weight=receipt.weight if receipt is not None else None,
                refresh_available=bool(diagnostics["refresh_available"]),
                refresh_retry_after_seconds=refresh_retry_after_seconds,
                configuration_message=None,
                reminder=reminder,
            )
        lane_state = (
            "active_estimated"
            if receipt.timing_source == "legacy_estimated"
            else "active_exact"
        )
        return self._build_status(
            lane_state=lane_state,
            active=True,
            voted_at_utc=receipt.vote_created_at,
            expires_at_utc=receipt.vote_expires_at,
            timing_source=receipt.timing_source,
            weight=receipt.weight,
            refresh_available=bool(diagnostics["refresh_available"]),
            refresh_retry_after_seconds=refresh_retry_after_seconds,
            configuration_message=None,
            reminder=reminder,
        )

    async def wish_character_limit(
        self,
        discord_user_id: int,
        *,
        now_utc: datetime | None = None,
    ) -> int:
        status = await self.get_vote_bonus_status(discord_user_id, now_utc=now_utc)
        return status.wish_character_limit

    async def timeline_entry_limit(
        self,
        discord_user_id: int,
        *,
        now_utc: datetime | None = None,
    ) -> int:
        status = await self.get_vote_bonus_status(discord_user_id, now_utc=now_utc)
        return status.timeline_entry_limit

    async def fetch_latest_vote_receipt(self, discord_user_id: int) -> TopggVoteReceipt | None:
        receipt = await self._repository.fetch_latest_topgg_vote_receipt(discord_user_id)
        if receipt is None:
            return None
        return self._coerce_vote_receipt(receipt)

    async def list_recent_vote_receipts(
        self,
        discord_user_id: int,
        *,
        limit: int = 5,
    ) -> list[TopggVoteReceipt]:
        receipts = await self._repository.list_recent_topgg_vote_receipts(
            discord_user_id,
            limit=limit,
        )
        if not receipts:
            return []
        return [self._coerce_vote_receipt(receipt) for receipt in receipts]

    async def fetch_vote_reminder(self, discord_user_id: int) -> TopggVoteReminder | None:
        fetcher = getattr(self._repository, "fetch_topgg_vote_reminder", None)
        if not callable(fetcher):
            return None
        reminder = await fetcher(discord_user_id)
        if reminder is None:
            return None
        return self._coerce_vote_reminder(reminder)

    async def set_vote_reminders_enabled(
        self,
        discord_user_id: int,
        *,
        enabled: bool,
        now_utc: datetime | None = None,
    ) -> VoteReminderUpdateResult:
        now = now_utc or datetime.now(UTC)
        updater = getattr(self._repository, "upsert_topgg_vote_reminder_preference", None)
        clearer = getattr(self._repository, "clear_topgg_vote_reminder_schedule", None)
        if not callable(updater) or not callable(clearer):
            status = await self.get_vote_bonus_status(discord_user_id, now_utc=now)
            return VoteReminderUpdateResult(
                status=status,
                note="Vote reminders are unavailable on this deployment right now.",
            )

        await updater(discord_user_id, enabled=enabled, now_utc=now)
        if not enabled:
            await clearer(discord_user_id, keep_enabled=False, now_utc=now)
            status = await self.get_vote_bonus_status(discord_user_id, now_utc=now)
            return VoteReminderUpdateResult(
                status=status,
                note="Vote reminders are off.",
            )

        latest_receipt = await self.fetch_latest_vote_receipt(discord_user_id)
        if latest_receipt is None or latest_receipt.vote_expires_at is None or latest_receipt.vote_expires_at <= now:
            await clearer(discord_user_id, keep_enabled=True, now_utc=now)
            status = await self.get_vote_bonus_status(discord_user_id, now_utc=now)
            return VoteReminderUpdateResult(
                status=status,
                note="Vote reminders are on. Your next valid vote will arm a reminder.",
            )

        note = await self._schedule_reminder_for_receipt(
            discord_user_id,
            latest_receipt,
            now_utc=now,
        )
        status = await self.get_vote_bonus_status(discord_user_id, now_utc=now)
        return VoteReminderUpdateResult(status=status, note=note)

    async def handle_webhook(
        self,
        *,
        headers: Mapping[str, str],
        raw_body: bytes,
        now_utc: datetime | None = None,
    ) -> TopggWebhookResult:
        now = now_utc or datetime.now(UTC)
        diagnostics = self.diagnostics_snapshot()
        if diagnostics["configuration_state"] == "disabled":
            return TopggWebhookResult(
                http_status=503,
                outcome="disabled",
                payload={
                    "status": "disabled",
                    "message": str(diagnostics["configuration_message"]),
                },
            )
        if diagnostics["configuration_state"] != "ready":
            return TopggWebhookResult(
                http_status=503,
                outcome="misconfigured",
                payload={
                    "status": "unavailable",
                    "message": str(diagnostics["configuration_message"]),
                },
            )

        normalized_headers = {key.lower(): value.strip() for key, value in headers.items()}
        webhook_mode = self._required_webhook_mode()
        try:
            if webhook_mode == "v2":
                return await self._handle_v2_webhook(
                    headers=normalized_headers,
                    raw_body=raw_body,
                    now_utc=now,
                )
            return await self._handle_legacy_webhook(
                headers=normalized_headers,
                raw_body=raw_body,
                now_utc=now,
            )
        except ValidationError:
            return TopggWebhookResult(
                http_status=400,
                outcome="invalid_payload",
                payload={"status": "invalid_payload"},
            )

    async def refresh_vote_status(
        self,
        discord_user_id: int,
        *,
        now_utc: datetime | None = None,
    ) -> VoteRefreshResult:
        now = now_utc or datetime.now(UTC)
        diagnostics = self.diagnostics_snapshot()
        current_status = await self.get_vote_bonus_status(discord_user_id, now_utc=now)
        if not diagnostics["refresh_available"]:
            return VoteRefreshResult(
                outcome="unavailable",
                status=current_status,
                note="Webhooks are active. Optional manual refresh is not configured on this deployment.",
            )
        retry_after_seconds = self._refresh_retry_after_seconds(discord_user_id, now_utc=now)
        if retry_after_seconds is not None:
            throttled_status = await self.get_vote_bonus_status(discord_user_id, now_utc=now)
            return VoteRefreshResult(
                outcome="cooldown",
                status=throttled_status,
                note=f"Refresh is cooling down for {retry_after_seconds} more second(s).",
            )

        self._refresh_last_called_at[discord_user_id] = now
        payload = await self._fetch_vote_status_by_user(discord_user_id)
        if payload is None:
            receipt = TopggVoteReceipt(
                event_id=f"refresh-miss:{discord_user_id}:{int(now.timestamp())}",
                discord_user_id=discord_user_id,
                event_type="vote.refresh_miss",
                webhook_mode=self._required_webhook_mode(),
                payload_hash="0" * 64,
                trace_id=None,
                signature_timestamp=None,
                vote_created_at=now,
                vote_expires_at=now,
                timing_source="exact",
                weight=0,
                received_at=now,
                processed_at=now,
                status="processed",
                error_text="No active Top.gg vote was reported by refresh.",
            )
            await self._repository.insert_topgg_vote_receipt(receipt)
            await self._clear_reminder_if_enabled(discord_user_id, now_utc=now)
            status = await self.get_vote_bonus_status(discord_user_id, now_utc=now)
            return VoteRefreshResult(
                outcome="not_found",
                status=status,
                note="Top.gg did not report an active vote right now.",
            )

        created_at = parse_topgg_datetime(str(payload.get("created_at")))
        expires_at = parse_topgg_datetime(str(payload.get("expires_at")))
        if created_at is None or expires_at is None or expires_at <= created_at:
            raise ValidationError("Top.gg refresh returned an invalid vote window.")
        weight = int(payload.get("weight", 1) or 1)
        canonical_id = f"refresh:{discord_user_id}:{expires_at.isoformat()}"
        receipt = TopggVoteReceipt(
            event_id=canonical_id,
            discord_user_id=discord_user_id,
            event_type="vote.refresh",
            webhook_mode=self._required_webhook_mode(),
            payload_hash=payload_sha256(json.dumps(payload, sort_keys=True).encode("utf-8")),
            trace_id=None,
            signature_timestamp=None,
            vote_created_at=created_at,
            vote_expires_at=expires_at,
            timing_source="exact",
            weight=weight,
            received_at=now,
            processed_at=now,
            status="processed",
        )
        inserted = await self._repository.insert_topgg_vote_receipt(receipt)
        if inserted:
            await self._sync_reminder_if_enabled(discord_user_id, receipt, now_utc=now)
        status = await self.get_vote_bonus_status(discord_user_id, now_utc=now)
        return VoteRefreshResult(
            outcome="refreshed",
            status=status,
            note="Vote status refreshed from Top.gg.",
        )

    async def _handle_v2_webhook(
        self,
        *,
        headers: dict[str, str],
        raw_body: bytes,
        now_utc: datetime,
    ) -> TopggWebhookResult:
        signature_parts = parse_signature_header(headers.get("x-topgg-signature"))
        if signature_parts is None:
            return TopggWebhookResult(
                http_status=400,
                outcome="invalid_signature",
                payload={"status": "invalid_signature"},
            )
        signature_timestamp, received_signature = signature_parts
        expected_signature = build_v2_signature(
            self._settings.topgg_webhook_secret,
            timestamp=signature_timestamp,
            payload=raw_body,
        )
        if not hmac_compare(expected_signature, received_signature):
            return TopggWebhookResult(
                http_status=400,
                outcome="invalid_signature",
                payload={"status": "invalid_signature"},
            )
        timestamp_utc = datetime.fromtimestamp(signature_timestamp, tz=UTC)
        age = abs((now_utc - timestamp_utc).total_seconds())
        if age > self._settings.topgg_v2_replay_window_seconds:
            return TopggWebhookResult(
                http_status=400,
                outcome="stale",
                payload={"status": "stale"},
            )
        payload = self._load_json_object(raw_body)
        event_type = str(payload.get("type") or "")
        if event_type == "webhook.test":
            return TopggWebhookResult(
                http_status=200,
                outcome="ignored_test",
                payload={"status": "ignored_test"},
            )
        if event_type != "vote.create":
            return TopggWebhookResult(
                http_status=400,
                outcome="invalid_payload",
                payload={"status": "invalid_payload"},
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            return TopggWebhookResult(
                http_status=400,
                outcome="invalid_payload",
                payload={"status": "invalid_payload"},
            )
        project = data.get("project")
        user = data.get("user")
        if not isinstance(project, dict) or not isinstance(user, dict):
            return TopggWebhookResult(
                http_status=400,
                outcome="invalid_payload",
                payload={"status": "invalid_payload"},
            )
        if str(project.get("platform_id")) != str(self._settings.topgg_bot_id):
            return TopggWebhookResult(
                http_status=400,
                outcome="invalid_payload",
                payload={"status": "invalid_payload"},
            )
        created_at = parse_topgg_datetime(_string_or_none(data.get("created_at")))
        expires_at = parse_topgg_datetime(_string_or_none(data.get("expires_at")))
        if created_at is None or expires_at is None or expires_at <= created_at:
            return TopggWebhookResult(
                http_status=400,
                outcome="invalid_payload",
                payload={"status": "invalid_payload"},
            )
        discord_user_id = int(str(user.get("platform_id") or "0"))
        if discord_user_id <= 0:
            return TopggWebhookResult(
                http_status=400,
                outcome="invalid_payload",
                payload={"status": "invalid_payload"},
            )
        receipt = TopggVoteReceipt(
            event_id=str(data.get("id")),
            discord_user_id=discord_user_id,
            event_type=event_type,
            webhook_mode="v2",
            payload_hash=payload_sha256(raw_body),
            trace_id=headers.get("x-request-id"),
            signature_timestamp=timestamp_utc,
            vote_created_at=created_at,
            vote_expires_at=expires_at,
            timing_source="exact",
            weight=int(data.get("weight", 1) or 1),
            received_at=now_utc,
            processed_at=now_utc,
            status="processed",
        )
        inserted = await self._repository.insert_topgg_vote_receipt(receipt)
        if not inserted:
            return TopggWebhookResult(
                http_status=200,
                outcome="duplicate",
                payload={"status": "duplicate"},
                receipt=receipt,
            )
        await self._sync_reminder_if_enabled(discord_user_id, receipt, now_utc=now_utc)
        return TopggWebhookResult(
            http_status=200,
            outcome="processed",
            payload={"status": "processed"},
            receipt=receipt,
        )

    async def _handle_legacy_webhook(
        self,
        *,
        headers: dict[str, str],
        raw_body: bytes,
        now_utc: datetime,
    ) -> TopggWebhookResult:
        if headers.get("authorization") != self._settings.topgg_webhook_secret:
            return TopggWebhookResult(
                http_status=400,
                outcome="invalid_signature",
                payload={"status": "invalid_signature"},
            )
        payload = self._load_json_object(raw_body)
        event_type = str(payload.get("type") or "")
        if event_type == "test":
            return TopggWebhookResult(
                http_status=200,
                outcome="ignored_test",
                payload={"status": "ignored_test"},
            )
        if event_type != "upvote":
            return TopggWebhookResult(
                http_status=400,
                outcome="invalid_payload",
                payload={"status": "invalid_payload"},
            )
        if str(payload.get("bot")) != str(self._settings.topgg_bot_id):
            return TopggWebhookResult(
                http_status=400,
                outcome="invalid_payload",
                payload={"status": "invalid_payload"},
            )
        discord_user_id = int(str(payload.get("user") or "0"))
        if discord_user_id <= 0:
            return TopggWebhookResult(
                http_status=400,
                outcome="invalid_payload",
                payload={"status": "invalid_payload"},
            )
        bucket = int(now_utc.timestamp()) // int(self.LEGACY_VOTE_WINDOW.total_seconds())
        receipt = TopggVoteReceipt(
            event_id=f"legacy:{discord_user_id}:{bucket}",
            discord_user_id=discord_user_id,
            event_type=event_type,
            webhook_mode="legacy",
            payload_hash=payload_sha256(raw_body),
            trace_id=None,
            signature_timestamp=None,
            vote_created_at=now_utc,
            vote_expires_at=now_utc + self.LEGACY_VOTE_WINDOW,
            timing_source="legacy_estimated",
            weight=2 if bool(payload.get("isWeekend")) else 1,
            received_at=now_utc,
            processed_at=now_utc,
            status="processed",
        )
        inserted = await self._repository.insert_topgg_vote_receipt(receipt)
        if not inserted:
            return TopggWebhookResult(
                http_status=200,
                outcome="duplicate",
                payload={"status": "duplicate"},
                receipt=receipt,
            )
        await self._sync_reminder_if_enabled(discord_user_id, receipt, now_utc=now_utc)
        return TopggWebhookResult(
            http_status=200,
            outcome="processed",
            payload={"status": "processed"},
            receipt=receipt,
        )

    async def _fetch_vote_status_by_user(self, discord_user_id: int) -> dict[str, object] | None:
        token = self._settings.topgg_token.strip()
        if not token:
            raise ValidationError("Top.gg refresh is unavailable because TOPGG_TOKEN is missing.")
        url = f"https://top.gg/api/v1/projects/@me/votes/{discord_user_id}"
        headers = {"Authorization": f"Bearer {token}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params={"source": "discord"}) as response:
                if response.status == 404:
                    return None
                if response.status in {401, 403}:
                    raise ValidationError(
                        "Top.gg refresh is unavailable because TOPGG_TOKEN was rejected."
                    )
                if response.status >= 400:
                    raise ValidationError(f"Top.gg refresh failed with status {response.status}.")
                payload = await response.json()
        if not isinstance(payload, dict):
            raise ValidationError("Top.gg refresh returned an unexpected payload.")
        return payload

    def _build_status(
        self,
        *,
        lane_state: str,
        active: bool,
        voted_at_utc: datetime | None,
        expires_at_utc: datetime | None,
        timing_source: str | None,
        weight: int | None,
        refresh_available: bool,
        refresh_retry_after_seconds: int | None,
        configuration_message: str | None,
        reminder: TopggVoteReminder | None,
    ) -> VoteBonusStatus:
        reminder_lane_state, next_reminder_at_utc, last_error_code, reminder_timing_source = (
            self._describe_reminder(reminder)
        )
        return VoteBonusStatus(
            lane_state=lane_state,  # type: ignore[arg-type]
            enabled=lane_state != "disabled",
            active=active,
            configuration_message=configuration_message,
            voted_at_utc=voted_at_utc,
            expires_at_utc=expires_at_utc,
            timing_source=timing_source,  # type: ignore[arg-type]
            weight=weight,
            refresh_available=refresh_available,
            refresh_cooldown_seconds=self._settings.topgg_refresh_cooldown_seconds,
            refresh_retry_after_seconds=refresh_retry_after_seconds,
            wish_character_limit=(
                self.BONUS_WISH_CHARACTER_LIMIT if active else self.DEFAULT_WISH_CHARACTER_LIMIT
            ),
            timeline_entry_limit=(
                self.BONUS_TIMELINE_ENTRY_LIMIT if active else self.DEFAULT_TIMELINE_ENTRY_LIMIT
            ),
            reminders_enabled=bool(reminder.enabled) if reminder is not None else False,
            reminder_lane_state=reminder_lane_state,
            next_reminder_at_utc=next_reminder_at_utc,
            last_reminder_error_code=last_error_code,
            reminder_timing_source=reminder_timing_source,  # type: ignore[arg-type]
        )

    async def _sync_reminder_if_enabled(
        self,
        discord_user_id: int,
        receipt: TopggVoteReceipt,
        *,
        now_utc: datetime,
    ) -> None:
        reminder = await self.fetch_vote_reminder(discord_user_id)
        if reminder is None or not reminder.enabled:
            return
        await self._schedule_reminder_for_receipt(discord_user_id, receipt, now_utc=now_utc)

    async def _clear_reminder_if_enabled(
        self,
        discord_user_id: int,
        *,
        now_utc: datetime,
    ) -> None:
        reminder = await self.fetch_vote_reminder(discord_user_id)
        if reminder is None or not reminder.enabled:
            return
        clearer = getattr(self._repository, "clear_topgg_vote_reminder_schedule", None)
        if callable(clearer):
            await clearer(discord_user_id, keep_enabled=True, now_utc=now_utc)

    async def _schedule_reminder_for_receipt(
        self,
        discord_user_id: int,
        receipt: TopggVoteReceipt,
        *,
        now_utc: datetime,
    ) -> str:
        reminder_at = self._compute_reminder_at(receipt, now_utc=now_utc)
        clearer = getattr(self._repository, "clear_topgg_vote_reminder_schedule", None)
        scheduler = getattr(self._repository, "schedule_topgg_vote_reminder", None)
        if reminder_at is None:
            if callable(clearer):
                await clearer(discord_user_id, keep_enabled=True, now_utc=now_utc)
            return "Vote reminders are on. This vote window is too close to expiry, so the next vote will arm a reminder."
        if callable(scheduler) and receipt.vote_expires_at is not None and receipt.timing_source is not None:
            await scheduler(
                discord_user_id,
                vote_expires_at=receipt.vote_expires_at,
                reminder_at=reminder_at,
                timing_source=receipt.timing_source,
                now_utc=now_utc,
            )
        if receipt.timing_source == "legacy_estimated":
            return "Vote reminders are on. A reminder is armed for this estimated vote window."
        return "Vote reminders are on. A reminder is armed before this vote window ends."

    def _compute_reminder_at(
        self,
        receipt: TopggVoteReceipt,
        *,
        now_utc: datetime,
    ) -> datetime | None:
        if receipt.vote_expires_at is None:
            return None
        remaining = receipt.vote_expires_at - now_utc
        if remaining > self.REMINDER_LEAD_TIME:
            return receipt.vote_expires_at - self.REMINDER_LEAD_TIME
        if remaining >= self.REMINDER_MINIMUM_REMAINING:
            return now_utc + self.REMINDER_LAST_MINUTE_DELAY
        return None

    def _describe_reminder(
        self,
        reminder: TopggVoteReminder | None,
    ) -> tuple[str, datetime | None, str | None, str | None]:
        if reminder is None or not reminder.enabled:
            return "off", None, None, None
        if reminder.scheduled_reminder_at is not None and reminder.scheduled_vote_expires_at is not None:
            if reminder.timing_source == "legacy_estimated":
                return (
                    "armed_estimated",
                    reminder.scheduled_reminder_at,
                    reminder.last_error_code,
                    reminder.timing_source,
                )
            return (
                "armed_exact",
                reminder.scheduled_reminder_at,
                reminder.last_error_code,
                reminder.timing_source,
            )
        if reminder.last_error_code:
            return "delivery_issue", None, reminder.last_error_code, None
        return "waiting_for_next_vote", None, None, None

    @staticmethod
    def _coerce_vote_receipt(value: object) -> TopggVoteReceipt:
        if isinstance(value, TopggVoteReceipt):
            return value
        try:
            return TopggVoteReceipt(
                event_id=str(getattr(value, "event_id")),
                discord_user_id=int(getattr(value, "discord_user_id")),
                event_type=str(getattr(value, "event_type")),
                webhook_mode=getattr(value, "webhook_mode"),
                payload_hash=str(getattr(value, "payload_hash")),
                trace_id=getattr(value, "trace_id", None),
                signature_timestamp=getattr(value, "signature_timestamp", None),
                vote_created_at=getattr(value, "vote_created_at", None),
                vote_expires_at=getattr(value, "vote_expires_at", None),
                timing_source=getattr(value, "timing_source", None),
                weight=int(getattr(value, "weight")),
                received_at=getattr(value, "received_at"),
                processed_at=getattr(value, "processed_at"),
                status=getattr(value, "status"),
                error_text=getattr(value, "error_text", None),
            )
        except Exception as exc:
            raise TypeError("Repository returned an unexpected vote receipt value.") from exc

    @staticmethod
    def _coerce_vote_reminder(value: object) -> TopggVoteReminder:
        if isinstance(value, TopggVoteReminder):
            return value
        try:
            return TopggVoteReminder(
                discord_user_id=int(getattr(value, "discord_user_id")),
                enabled=bool(getattr(value, "enabled")),
                scheduled_vote_expires_at=getattr(value, "scheduled_vote_expires_at", None),
                scheduled_reminder_at=getattr(value, "scheduled_reminder_at", None),
                processing_started_at=getattr(value, "processing_started_at", None),
                last_reminded_vote_expires_at=getattr(value, "last_reminded_vote_expires_at", None),
                last_reminded_at=getattr(value, "last_reminded_at", None),
                attempt_count=int(getattr(value, "attempt_count", 0)),
                last_error_code=getattr(value, "last_error_code", None),
                timing_source=getattr(value, "timing_source", None),
                created_at=getattr(value, "created_at"),
                updated_at=getattr(value, "updated_at"),
            )
        except Exception as exc:
            raise TypeError("Repository returned an unexpected vote reminder value.") from exc

    def _required_webhook_mode(self) -> TopggWebhookMode:
        return "v2" if is_v2_webhook_secret(self._settings.topgg_webhook_secret) else "legacy"

    @staticmethod
    def _webhook_mode(secret: str) -> TopggWebhookMode | None:
        if not secret:
            return None
        return "v2" if is_v2_webhook_secret(secret) else "legacy"

    @staticmethod
    def _load_json_object(raw_body: bytes) -> dict[str, object]:
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("Top.gg sent malformed JSON.") from exc
        if not isinstance(payload, dict):
            raise ValidationError("Top.gg sent an unexpected webhook payload.")
        return payload

    def _refresh_retry_after_seconds(
        self,
        discord_user_id: int,
        *,
        now_utc: datetime,
    ) -> int | None:
        last_called_at = self._refresh_last_called_at.get(discord_user_id)
        if last_called_at is None:
            return None
        remaining = self._settings.topgg_refresh_cooldown_seconds - int(
            (now_utc - last_called_at).total_seconds()
        )
        return remaining if remaining > 0 else None


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def hmac_compare(expected: str, received: str) -> bool:
    import hmac

    return hmac.compare_digest(expected.lower(), received.lower())
