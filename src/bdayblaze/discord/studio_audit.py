from __future__ import annotations

from datetime import UTC, datetime, timedelta

import discord

from bdayblaze.discord.embed_budget import BudgetedEmbed
from bdayblaze.logging import get_logger, redact_identifier
from bdayblaze.services.content_policy import ContentPolicyError
from bdayblaze.services.settings_service import SettingsService


class StudioAuditLogger:
    def __init__(self, settings_service: SettingsService, *, dedupe_ttl_seconds: int = 300) -> None:
        self._settings_service = settings_service
        self._dedupe_ttl = timedelta(seconds=dedupe_ttl_seconds)
        self._seen_attempts: dict[str, datetime] = {}
        self._logger = get_logger(component="studio_audit")

    async def log_blocked_attempt(
        self,
        interaction: discord.Interaction,
        *,
        surface: str,
        error: ContentPolicyError,
    ) -> None:
        categories = tuple(
            sorted({violation.category_label for violation in error.violations})
        )
        fields = tuple(sorted({violation.field_label for violation in error.violations}))
        rule_codes = tuple(sorted({violation.rule_code for violation in error.violations}))
        await self.log_blocked_fields(
            interaction,
            surface=surface,
            field_labels=fields,
            category_labels=categories,
            rule_codes=rule_codes,
        )

    async def log_blocked_fields(
        self,
        interaction: discord.Interaction,
        *,
        surface: str,
        field_labels: tuple[str, ...],
        category_labels: tuple[str, ...],
        rule_codes: tuple[str, ...],
    ) -> None:
        if interaction.guild is None:
            return
        settings = await self._settings_service.get_settings(interaction.guild.id)
        channel_id = settings.studio_audit_channel_id
        if channel_id is None:
            return

        fingerprint = self._fingerprint(
            interaction,
            surface=surface,
            field_labels=field_labels,
            rule_codes=rule_codes,
        )
        now_utc = datetime.now(UTC)
        self._prune(now_utc)
        if fingerprint in self._seen_attempts:
            return

        channel = interaction.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        bot_member = interaction.guild.me
        if bot_member is None:
            return
        permissions = channel.permissions_for(bot_member)
        if (
            not permissions.view_channel
            or not permissions.send_messages
            or not permissions.embed_links
        ):
            return

        categories = ", ".join(category_labels)
        fields = ", ".join(field_labels)
        budget = BudgetedEmbed.create(
            title="Studio safety block",
            description="A blocked Studio/admin content change was rejected by policy.",
            color=discord.Color.orange(),
            timestamp=now_utc,
        )
        budget.add_field(name="Actor", value=interaction.user.mention, inline=False)
        budget.add_field(name="Surface", value=surface, inline=True)
        budget.add_field(name="Fields", value=fields, inline=True)
        budget.add_field(name="Category", value=categories, inline=False)
        budget.set_footer("Raw blocked content was intentionally not logged.")
        try:
            await channel.send(
                embed=budget.build(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            self._logger.warning(
                "studio_audit_send_failed",
                guild_id=redact_identifier(interaction.guild.id),
                channel_id=redact_identifier(channel_id),
                surface=surface,
            )
            return
        self._seen_attempts[fingerprint] = now_utc

    def _fingerprint(
        self,
        interaction: discord.Interaction,
        *,
        surface: str,
        field_labels: tuple[str, ...],
        rule_codes: tuple[str, ...],
    ) -> str:
        codes = ",".join(rule_codes)
        fields = ",".join(field_labels)
        return f"{interaction.guild_id}:{interaction.user.id}:{surface}:{fields}:{codes}"

    def _prune(self, now_utc: datetime) -> None:
        expired = [
            key
            for key, seen_at in self._seen_attempts.items()
            if now_utc - seen_at > self._dedupe_ttl
        ]
        for key in expired:
            self._seen_attempts.pop(key, None)
