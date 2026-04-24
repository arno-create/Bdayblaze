from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import aiohttp

from bdayblaze.config import Settings
from bdayblaze.domain.topgg import (
    TopggVoteReceipt,
    TopggWebhookMode,
    TopggWebhookResult,
    VoteBonusStatus,
    VoteRefreshResult,
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

    def __init__(
        self,
        repository: object,
        *,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._refresh_last_called_at: dict[int, datetime] = {}

    @property
    def vote_url(self) -> str:
        return self.TOPGG_VOTE_URL_TEMPLATE.format(bot_id=self._settings.topgg_bot_id)

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
                    "Top.gg vote bonus is enabled. Webhooks are ready. "
                    "Manual refresh is unavailable until TOPGG_TOKEN is set."
                )
        refresh_available = enabled and state == "ready" and bool(self._settings.topgg_token)
        return {
            "enabled": enabled,
            "configuration_state": state,
            "configuration_message": message,
            "webhook_mode": webhook_mode,
            "storage_ready": True,
            "storage_backend": "postgres",
            "refresh_available": refresh_available,
            "refresh_cooldown_seconds": self._settings.topgg_refresh_cooldown_seconds,
            "timing_source": (
                "exact"
                if webhook_mode == "v2"
                else "legacy_estimated"
                if webhook_mode == "legacy"
                else None
            ),
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
        if diagnostics["configuration_state"] == "disabled":
            return self._build_status(
                lane_state="disabled",
                active=False,
                voted_at_utc=None,
                expires_at_utc=None,
                timing_source=None,
                weight=None,
                refresh_available=False,
                refresh_retry_after_seconds=None,
                configuration_message=str(diagnostics["configuration_message"]),
            )
        if diagnostics["configuration_state"] != "ready":
            return self._build_status(
                lane_state="misconfigured",
                active=False,
                voted_at_utc=None,
                expires_at_utc=None,
                timing_source=None,
                weight=None,
                refresh_available=False,
                refresh_retry_after_seconds=None,
                configuration_message=str(diagnostics["configuration_message"]),
            )
        receipt = await self.fetch_latest_vote_receipt(discord_user_id)
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
        if isinstance(receipt, TopggVoteReceipt):
            return receipt
        raise TypeError("Repository returned an unexpected vote receipt value.")

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
        return [receipt for receipt in receipts if isinstance(receipt, TopggVoteReceipt)]

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

        normalized_headers = {
            key.lower(): value.strip()
            for key, value in headers.items()
        }
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
                note="Refresh is unavailable because TOPGG_TOKEN is not configured.",
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
        await self._repository.insert_topgg_vote_receipt(receipt)
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
                    raise ValidationError(
                        f"Top.gg refresh failed with status {response.status}."
                    )
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
    ) -> VoteBonusStatus:
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
                self.BONUS_TIMELINE_ENTRY_LIMIT
                if active
                else self.DEFAULT_TIMELINE_ENTRY_LIMIT
            ),
        )

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
