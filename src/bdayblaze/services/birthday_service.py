from __future__ import annotations

import csv
import hashlib
import io
from datetime import UTC, datetime

from bdayblaze.domain.announcement_template import validate_announcement_template
from bdayblaze.domain.birthday_logic import (
    active_window_candidate_birthdays,
    anniversary_month_day,
    is_birthday_active_now,
    next_occurrence_at_utc,
    validate_birth_date,
    validate_timezone,
)
from bdayblaze.domain.models import (
    BirthdayImportError,
    BirthdayImportPreview,
    BirthdayImportRow,
    BirthdayPreview,
    MemberBirthday,
    ProfileVisibility,
    RecurringCelebration,
    TrackedAnniversary,
)
from bdayblaze.repositories.postgres import PostgresRepository
from bdayblaze.services.content_policy import ensure_safe_event_name, ensure_safe_template
from bdayblaze.services.errors import NotFoundError, ValidationError

_VALID_VISIBILITY_VALUES = {"private", "server_visible"}
_IMPORT_HEADERS = (
    "user_id",
    "month",
    "day",
    "birth_year",
    "timezone_override",
    "visibility",
)


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
        profile_visibility: ProfileVisibility = "private",
        now_utc: datetime | None = None,
    ) -> MemberBirthday:
        try:
            validate_birth_date(month, day)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        effective_now = now_utc or datetime.now(UTC)
        if birth_year is not None and (birth_year < 1900 or birth_year > effective_now.year):
            raise ValidationError("Birth year must be between 1900 and the current year.")
        normalized_visibility = _validate_profile_visibility(profile_visibility)
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
            profile_visibility=normalized_visibility,
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
        self,
        guild_id: int,
        limit: int = 10,
        *,
        visible_only: bool,
    ) -> list[BirthdayPreview]:
        return await self._repository.list_upcoming_birthdays(
            guild_id,
            limit,
            visible_only=visible_only,
        )

    async def list_birthdays_for_month(
        self,
        guild_id: int,
        *,
        month: int,
        limit: int,
        order_by_upcoming: bool,
        visible_only: bool,
    ) -> list[BirthdayPreview]:
        return await self._repository.list_birthdays_for_month(
            guild_id,
            month,
            limit,
            order_by_upcoming=order_by_upcoming,
            visible_only=visible_only,
        )

    async def list_birthdays(
        self,
        guild_id: int,
        *,
        limit: int,
        order_by_upcoming: bool,
        visible_only: bool,
    ) -> list[BirthdayPreview]:
        return await self._repository.list_birthdays(
            guild_id,
            limit,
            order_by_upcoming=order_by_upcoming,
            visible_only=visible_only,
        )

    async def list_current_birthdays(
        self,
        guild_id: int,
        *,
        limit: int,
        visible_only: bool,
        now_utc: datetime | None = None,
    ) -> list[BirthdayPreview]:
        effective_now = now_utc or datetime.now(UTC)
        candidates = await self._repository.list_birthdays_for_month_day_pairs(
            guild_id,
            active_window_candidate_birthdays(effective_now),
            max(limit * 3, limit),
            visible_only=visible_only,
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
        visible_only: bool,
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
            visible_only=visible_only,
        )
        twins = [preview for preview in matches if preview.user_id != user_id]
        return birthday, twins[:limit]

    async def month_leaderboard(
        self,
        guild_id: int,
        *,
        month: int,
        visible_only: bool,
        limit: int = 3,
    ) -> list[tuple[int, int]]:
        return await self._repository.count_birthdays_by_day_for_month(
            guild_id,
            month,
            visible_only=visible_only,
            limit=limit,
        )

    async def export_birthdays_csv(self, guild_id: int) -> str:
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(_IMPORT_HEADERS)
        for birthday in await self._repository.list_member_birthdays_for_export(guild_id):
            writer.writerow(
                [
                    birthday.user_id,
                    birthday.birth_month,
                    birthday.birth_day,
                    birthday.birth_year or "",
                    birthday.timezone_override or "",
                    birthday.profile_visibility,
                ]
            )
        return output.getvalue()

    async def preview_birthdays_import(
        self,
        guild_id: int,
        csv_text: str,
        *,
        allowed_user_ids: set[int] | None = None,
    ) -> BirthdayImportPreview:
        rows: list[BirthdayImportRow] = []
        errors: list[BirthdayImportError] = []
        seen_user_ids: set[int] = set()
        reader = csv.DictReader(io.StringIO(csv_text))
        if reader.fieldnames != list(_IMPORT_HEADERS):
            raise ValidationError(
                "CSV headers must be exactly: "
                "user_id,month,day,birth_year,timezone_override,visibility."
            )
        for row_number, row in enumerate(reader, start=2):
            try:
                parsed = self._parse_import_row(row_number, row)
            except ValidationError as exc:
                errors.append(BirthdayImportError(row_number=row_number, message=str(exc)))
                continue
            if parsed.user_id in seen_user_ids:
                errors.append(
                    BirthdayImportError(
                        row_number=row_number,
                        message="Duplicate user_id rows are not allowed in one import.",
                    )
                )
                continue
            if allowed_user_ids is not None and parsed.user_id not in allowed_user_ids:
                errors.append(
                    BirthdayImportError(
                        row_number=row_number,
                        message="That user_id is not a current member of this server.",
                    )
                )
                continue
            seen_user_ids.add(parsed.user_id)
            rows.append(parsed)
        apply_token = _build_import_apply_token(guild_id, csv_text)
        return BirthdayImportPreview(
            total_rows=len(rows) + len(errors),
            valid_rows=tuple(rows),
            errors=tuple(errors),
            apply_token=apply_token,
        )

    async def apply_birthdays_import(
        self,
        guild_id: int,
        *,
        csv_text: str,
        apply_token: str,
        now_utc: datetime | None = None,
        allowed_user_ids: set[int] | None = None,
    ) -> BirthdayImportPreview:
        preview = await self.preview_birthdays_import(
            guild_id,
            csv_text,
            allowed_user_ids=allowed_user_ids,
        )
        if preview.apply_token != apply_token:
            raise ValidationError("Import apply token did not match this CSV preview.")
        for row in preview.valid_rows:
            await self.set_birthday(
                guild_id=guild_id,
                user_id=row.user_id,
                month=row.birth_month,
                day=row.birth_day,
                birth_year=row.birth_year,
                timezone_override=row.timezone_override,
                profile_visibility=row.profile_visibility,
                now_utc=now_utc,
            )
        return preview

    async def list_member_birthday_user_ids(
        self,
        guild_id: int,
        *,
        limit: int = 5000,
    ) -> list[int]:
        return await self._repository.list_member_birthday_user_ids(guild_id, limit=limit)

    async def sync_member_anniversary(
        self,
        *,
        guild_id: int,
        user_id: int,
        joined_at_utc: datetime | None,
        source: str,
        now_utc: datetime | None = None,
    ) -> TrackedAnniversary:
        if joined_at_utc is None:
            raise ValidationError("Discord did not provide a join date for that member.")
        settings = await self._repository.fetch_guild_settings(guild_id)
        timezone_name = settings.default_timezone if settings is not None else "UTC"
        month, day = anniversary_month_day(joined_at_utc, timezone_name)
        anniversary = TrackedAnniversary(
            guild_id=guild_id,
            user_id=user_id,
            joined_at_utc=joined_at_utc,
            next_occurrence_at_utc=next_occurrence_at_utc(
                birth_month=month,
                birth_day=day,
                timezone_name=timezone_name,
                now_utc=now_utc or datetime.now(UTC),
            ),
            source=source,
        )
        return await self._repository.upsert_tracked_anniversary(anniversary)

    async def upsert_recurring_celebration(
        self,
        *,
        guild_id: int,
        celebration_id: int | None,
        name: str,
        month: int,
        day: int,
        channel_id: int | None,
        template: str | None,
        enabled: bool,
        now_utc: datetime | None = None,
    ) -> RecurringCelebration:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValidationError("Recurring event name cannot be blank.")
        try:
            validate_birth_date(month, day)
            normalized_template = validate_announcement_template(
                template,
                kind="recurring_event",
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        ensure_safe_event_name(normalized_name)
        ensure_safe_template(normalized_template, label="Recurring event template")
        settings = await self._repository.fetch_guild_settings(guild_id)
        timezone_name = settings.default_timezone if settings is not None else "UTC"
        next_occurrence = next_occurrence_at_utc(
            birth_month=month,
            birth_day=day,
            timezone_name=timezone_name,
            now_utc=now_utc or datetime.now(UTC),
        )
        if celebration_id is None:
            return await self._repository.insert_recurring_celebration(
                guild_id=guild_id,
                name=normalized_name,
                event_month=month,
                event_day=day,
                channel_id=channel_id,
                template=normalized_template,
                enabled=enabled,
                celebration_kind="custom",
                use_guild_created_date=False,
                next_occurrence_at_utc=next_occurrence,
            )
        updated = await self._repository.update_recurring_celebration(
            celebration_id,
            guild_id=guild_id,
            name=normalized_name,
            event_month=month,
            event_day=day,
            channel_id=channel_id,
            template=normalized_template,
            enabled=enabled,
            celebration_kind="custom",
            use_guild_created_date=False,
            next_occurrence_at_utc=next_occurrence,
        )
        if updated is None:
            raise NotFoundError("That recurring event was not found in this server.")
        return updated

    async def get_server_anniversary(self, guild_id: int) -> RecurringCelebration | None:
        return await self._repository.fetch_server_anniversary(guild_id)

    async def upsert_server_anniversary(
        self,
        *,
        guild_id: int,
        guild_created_at_utc: datetime | None,
        override_month: int | None,
        override_day: int | None,
        channel_id: int | None,
        template: str | None,
        enabled: bool,
        use_guild_created_date: bool,
        now_utc: datetime | None = None,
    ) -> RecurringCelebration:
        settings = await self._repository.fetch_guild_settings(guild_id)
        timezone_name = settings.default_timezone if settings is not None else "UTC"
        if use_guild_created_date:
            if guild_created_at_utc is None:
                raise ValidationError(
                    "Discord did not provide this server's creation date. "
                    "Set a custom date instead."
                )
            month, day = anniversary_month_day(guild_created_at_utc, timezone_name)
        else:
            if override_month is None or override_day is None:
                raise ValidationError("Provide both a month and a day for the server anniversary.")
            try:
                validate_birth_date(override_month, override_day)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            month, day = override_month, override_day

        try:
            normalized_template = validate_announcement_template(
                template,
                kind="server_anniversary",
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        ensure_safe_template(normalized_template, label="Server anniversary template")

        next_occurrence = next_occurrence_at_utc(
            birth_month=month,
            birth_day=day,
            timezone_name=timezone_name,
            now_utc=now_utc or datetime.now(UTC),
        )
        existing = await self._repository.fetch_server_anniversary(guild_id)
        if existing is None:
            return await self._repository.insert_recurring_celebration(
                guild_id=guild_id,
                name="Server anniversary",
                event_month=month,
                event_day=day,
                channel_id=channel_id,
                template=normalized_template,
                enabled=enabled,
                celebration_kind="server_anniversary",
                use_guild_created_date=use_guild_created_date,
                next_occurrence_at_utc=next_occurrence,
            )
        updated = await self._repository.update_recurring_celebration(
            existing.id,
            guild_id=guild_id,
            name="Server anniversary",
            event_month=month,
            event_day=day,
            channel_id=channel_id,
            template=normalized_template,
            enabled=enabled,
            celebration_kind="server_anniversary",
            use_guild_created_date=use_guild_created_date,
            next_occurrence_at_utc=next_occurrence,
        )
        if updated is None:
            raise NotFoundError("The server anniversary could not be updated.")
        return updated

    async def reset_server_anniversary(
        self,
        *,
        guild_id: int,
        guild_created_at_utc: datetime | None,
        enabled: bool,
        now_utc: datetime | None = None,
    ) -> RecurringCelebration:
        return await self.upsert_server_anniversary(
            guild_id=guild_id,
            guild_created_at_utc=guild_created_at_utc,
            override_month=None,
            override_day=None,
            channel_id=None,
            template=None,
            enabled=enabled,
            use_guild_created_date=True,
            now_utc=now_utc,
        )

    async def list_recurring_celebrations(
        self,
        guild_id: int,
        *,
        limit: int = 20,
        include_server_anniversary: bool = False,
    ) -> list[RecurringCelebration]:
        return await self._repository.list_recurring_celebrations(
            guild_id,
            limit=limit,
            include_server_anniversary=include_server_anniversary,
        )

    async def get_recurring_celebration(
        self,
        guild_id: int,
        celebration_id: int,
    ) -> RecurringCelebration:
        celebration = await self._repository.fetch_recurring_celebration(guild_id, celebration_id)
        if celebration is None:
            raise NotFoundError("That recurring event was not found in this server.")
        return celebration

    async def remove_recurring_celebration(
        self,
        guild_id: int,
        celebration_id: int,
    ) -> RecurringCelebration:
        deleted = await self._repository.delete_recurring_celebration(guild_id, celebration_id)
        if deleted is None:
            raise NotFoundError("That recurring event was not found in this server.")
        return deleted

    def _parse_import_row(
        self,
        row_number: int,
        row: dict[str, str | None],
    ) -> BirthdayImportRow:
        try:
            user_id = int((row.get("user_id") or "").strip())
            month = int((row.get("month") or "").strip())
            day = int((row.get("day") or "").strip())
        except ValueError as exc:
            raise ValidationError("user_id, month, and day must be integers.") from exc
        birth_year_raw = (row.get("birth_year") or "").strip()
        birth_year = int(birth_year_raw) if birth_year_raw else None
        timezone_override = (row.get("timezone_override") or "").strip() or None
        visibility = _validate_profile_visibility((row.get("visibility") or "").strip())
        try:
            validate_birth_date(month, day)
            if birth_year is not None and birth_year < 1900:
                raise ValidationError("birth_year must be blank or at least 1900.")
            if timezone_override is not None:
                validate_timezone(timezone_override)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return BirthdayImportRow(
            row_number=row_number,
            user_id=user_id,
            birth_month=month,
            birth_day=day,
            birth_year=birth_year,
            timezone_override=timezone_override,
            profile_visibility=visibility,
        )


def _validate_profile_visibility(value: str) -> ProfileVisibility:
    if value not in _VALID_VISIBILITY_VALUES:
        raise ValidationError("Visibility must be 'private' or 'server_visible'.")
    return value  # type: ignore[return-value]


def _build_import_apply_token(guild_id: int, csv_text: str) -> str:
    digest = hashlib.sha256(f"{guild_id}\n{csv_text}".encode()).hexdigest()
    return digest[:12]
