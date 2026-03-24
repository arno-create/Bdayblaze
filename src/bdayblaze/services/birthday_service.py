from __future__ import annotations

from datetime import UTC, datetime

from bdayblaze.domain.birthday_logic import (
    active_window_candidate_birthdays,
    is_birthday_active_now,
    next_occurrence_at_utc,
    validate_birth_date,
    validate_timezone,
)
from bdayblaze.domain.models import BirthdayPreview, MemberBirthday
from bdayblaze.repositories.postgres import PostgresRepository
from bdayblaze.services.errors import NotFoundError, ValidationError


class BirthdayService:
    def __init__(self, repository: PostgresRepository) -> None:
        self._repository = repository

    async def set_birthday(
        self,
        *,
        guild_id: int,
        user_id: int,
        month: int,
        day: int,
        birth_year: int | None,
        timezone_override: str | None,
        now_utc: datetime | None = None,
    ) -> MemberBirthday:
        try:
            validate_birth_date(month, day)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        effective_now = now_utc or datetime.now(UTC)
        if birth_year is not None and (birth_year < 1900 or birth_year > effective_now.year):
            raise ValidationError("Birth year must be between 1900 and the current year.")
        settings = await self._repository.fetch_guild_settings(guild_id)
        normalized_timezone = timezone_override.strip() if timezone_override else None
        if normalized_timezone == "":
            normalized_timezone = None
        effective_timezone = normalized_timezone or (
            settings.default_timezone if settings else "UTC"
        )
        try:
            validate_timezone(effective_timezone)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        existing = await self._repository.fetch_member_birthday(guild_id, user_id)
        birthday = MemberBirthday(
            guild_id=guild_id,
            user_id=user_id,
            birth_month=month,
            birth_day=day,
            birth_year=birth_year,
            timezone_override=normalized_timezone,
            age_visible=False if existing is None else existing.age_visible,
            next_occurrence_at_utc=next_occurrence_at_utc(
                birth_month=month,
                birth_day=day,
                timezone_name=effective_timezone,
                now_utc=effective_now,
            ),
            next_role_removal_at_utc=existing.next_role_removal_at_utc if existing else None,
            active_birthday_role_id=existing.active_birthday_role_id if existing else None,
        )
        return await self._repository.upsert_member_birthday(birthday)

    async def get_birthday(self, guild_id: int, user_id: int) -> MemberBirthday:
        return await self.require_birthday(
            guild_id,
            user_id,
            missing_message="You have not registered a birthday in this server yet.",
        )

    async def require_birthday(
        self,
        guild_id: int,
        user_id: int,
        *,
        missing_message: str,
    ) -> MemberBirthday:
        birthday = await self._repository.fetch_member_birthday(guild_id, user_id)
        if birthday is None:
            raise NotFoundError(missing_message)
        return birthday

    async def remove_birthday(self, guild_id: int, user_id: int) -> MemberBirthday:
        return await self.remove_member_birthday(
            guild_id,
            user_id,
            missing_message="You do not have stored birthday data in this server.",
        )

    async def remove_member_birthday(
        self,
        guild_id: int,
        user_id: int,
        *,
        missing_message: str,
    ) -> MemberBirthday:
        deleted = await self._repository.delete_member_birthday(guild_id, user_id)
        if deleted is None:
            raise NotFoundError(missing_message)
        return deleted

    async def list_upcoming_birthdays(
        self, guild_id: int, limit: int = 10
    ) -> list[BirthdayPreview]:
        return await self._repository.list_upcoming_birthdays(guild_id, limit)

    async def list_birthdays_for_month(
        self,
        guild_id: int,
        *,
        month: int,
        limit: int,
        order_by_upcoming: bool,
    ) -> list[BirthdayPreview]:
        return await self._repository.list_birthdays_for_month(
            guild_id,
            month,
            limit,
            order_by_upcoming=order_by_upcoming,
        )

    async def list_birthdays(
        self,
        guild_id: int,
        *,
        limit: int,
        order_by_upcoming: bool,
    ) -> list[BirthdayPreview]:
        return await self._repository.list_birthdays(
            guild_id,
            limit,
            order_by_upcoming=order_by_upcoming,
        )

    async def list_current_birthdays(
        self,
        guild_id: int,
        *,
        limit: int,
        now_utc: datetime | None = None,
    ) -> list[BirthdayPreview]:
        effective_now = now_utc or datetime.now(UTC)
        candidates = await self._repository.list_birthdays_for_month_day_pairs(
            guild_id,
            active_window_candidate_birthdays(effective_now),
            max(limit * 3, limit),
        )
        active = [
            preview
            for preview in candidates
            if is_birthday_active_now(
                birth_month=preview.birth_month,
                birth_day=preview.birth_day,
                timezone_name=preview.effective_timezone,
                now_utc=effective_now,
            )
        ]
        active.sort(key=lambda preview: preview.next_occurrence_at_utc)
        return active[:limit]

    async def list_birthday_twins(
        self,
        guild_id: int,
        user_id: int,
        *,
        limit: int,
    ) -> tuple[MemberBirthday, list[BirthdayPreview]]:
        birthday = await self.require_birthday(
            guild_id,
            user_id,
            missing_message=(
                "Save your birthday first, then you can look for birthday twins in this server."
            ),
        )
        matches = await self._repository.list_birthdays_for_month_day_pairs(
            guild_id,
            ((birthday.birth_month, birthday.birth_day),),
            limit + 1,
        )
        twins = [preview for preview in matches if preview.user_id != user_id]
        return birthday, twins[:limit]
