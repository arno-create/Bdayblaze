from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bdayblaze.domain.models import GuildSettings, MemberBirthday
from bdayblaze.services.birthday_service import BirthdayService
from bdayblaze.services.errors import ValidationError


class FakeImportRepository:
    def __init__(self) -> None:
        self.settings = GuildSettings.default(1)
        self.existing_birthdays: dict[int, MemberBirthday] = {}
        self.saved_birthdays: list[MemberBirthday] = []
        self.export_rows: list[MemberBirthday] = []
        self.user_ids: list[int] = [11, 22]

    async def fetch_guild_settings(self, guild_id: int) -> GuildSettings | None:
        return self.settings

    async def fetch_member_birthday(self, guild_id: int, user_id: int) -> MemberBirthday | None:
        return self.existing_birthdays.get(user_id)

    async def upsert_member_birthday(self, birthday: MemberBirthday) -> MemberBirthday:
        self.saved_birthdays.append(birthday)
        self.existing_birthdays[birthday.user_id] = birthday
        return birthday

    async def list_member_birthdays_for_export(self, guild_id: int) -> list[MemberBirthday]:
        return self.export_rows

    async def list_member_birthday_user_ids(self, guild_id: int, limit: int) -> list[int]:
        return self.user_ids[:limit]


def _birthday(user_id: int, visibility: str = "private") -> MemberBirthday:
    return MemberBirthday(
        guild_id=1,
        user_id=user_id,
        birth_month=3,
        birth_day=24,
        birth_year=1998,
        timezone_override="Europe/Berlin",
        profile_visibility=visibility,  # type: ignore[arg-type]
        next_occurrence_at_utc=datetime(2027, 3, 24, tzinfo=UTC),
        next_role_removal_at_utc=None,
        active_birthday_role_id=None,
    )


@pytest.mark.asyncio
async def test_export_birthdays_csv_includes_visibility_column() -> None:
    repository = FakeImportRepository()
    repository.export_rows = [_birthday(11, "private"), _birthday(22, "server_visible")]
    service = BirthdayService(repository)  # type: ignore[arg-type]

    csv_text = await service.export_birthdays_csv(1)

    assert csv_text.splitlines()[0] == "user_id,month,day,birth_year,timezone_override,visibility"
    assert "11,3,24,1998,Europe/Berlin,private" in csv_text
    assert "22,3,24,1998,Europe/Berlin,server_visible" in csv_text


@pytest.mark.asyncio
async def test_preview_birthdays_import_rejects_duplicate_user_ids() -> None:
    service = BirthdayService(FakeImportRepository())  # type: ignore[arg-type]
    csv_text = (
        "user_id,month,day,birth_year,timezone_override,visibility\n"
        "11,3,24,1998,UTC,private\n"
        "11,4,25,1999,UTC,server_visible\n"
    )

    preview = await service.preview_birthdays_import(1, csv_text)

    assert len(preview.valid_rows) == 1
    assert len(preview.errors) == 1
    assert "Duplicate user_id rows" in preview.errors[0].message


@pytest.mark.asyncio
async def test_preview_birthdays_import_rejects_unknown_members() -> None:
    service = BirthdayService(FakeImportRepository())  # type: ignore[arg-type]
    csv_text = (
        "user_id,month,day,birth_year,timezone_override,visibility\n"
        "11,3,24,1998,UTC,private\n"
        "99,4,25,1999,UTC,server_visible\n"
    )

    preview = await service.preview_birthdays_import(1, csv_text, allowed_user_ids={11})

    assert [row.user_id for row in preview.valid_rows] == [11]
    assert len(preview.errors) == 1
    assert "not a current member" in preview.errors[0].message


@pytest.mark.asyncio
async def test_apply_birthdays_import_requires_matching_token() -> None:
    service = BirthdayService(FakeImportRepository())  # type: ignore[arg-type]
    csv_text = (
        "user_id,month,day,birth_year,timezone_override,visibility\n11,3,24,1998,UTC,private\n"
    )

    with pytest.raises(ValidationError, match="did not match"):
        await service.apply_birthdays_import(
            1,
            csv_text=csv_text,
            apply_token="wrong-token",
            allowed_user_ids={11},
        )


@pytest.mark.asyncio
async def test_apply_birthdays_import_saves_valid_rows() -> None:
    repository = FakeImportRepository()
    service = BirthdayService(repository)  # type: ignore[arg-type]
    csv_text = (
        "user_id,month,day,birth_year,timezone_override,visibility\n"
        "11,3,24,1998,UTC,private\n"
        "22,4,25,,Asia/Yerevan,server_visible\n"
    )
    preview = await service.preview_birthdays_import(1, csv_text, allowed_user_ids={11, 22})

    applied = await service.apply_birthdays_import(
        1,
        csv_text=csv_text,
        apply_token=preview.apply_token,
        allowed_user_ids={11, 22},
        now_utc=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert len(applied.valid_rows) == 2
    assert [birthday.user_id for birthday in repository.saved_birthdays] == [11, 22]
    assert repository.saved_birthdays[1].profile_visibility == "server_visible"


@pytest.mark.asyncio
async def test_list_member_birthday_user_ids_delegates_to_repository() -> None:
    service = BirthdayService(FakeImportRepository())  # type: ignore[arg-type]

    assert await service.list_member_birthday_user_ids(1) == [11, 22]
