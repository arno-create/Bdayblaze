from __future__ import annotations

import asyncio
from collections.abc import Iterable

import discord


class MemberResolutionError(Exception):
    pass


async def resolve_guild_members(
    guild: discord.Guild,
    user_ids: Iterable[int],
    *,
    concurrency: int = 4,
    raise_on_http_error: bool = False,
) -> list[tuple[int, discord.Member]]:
    ordered_ids: list[int] = []
    seen_ids: set[int] = set()
    for user_id in user_ids:
        if user_id in seen_ids:
            continue
        seen_ids.add(user_id)
        ordered_ids.append(user_id)

    resolved: dict[int, discord.Member] = {}
    unresolved_ids: list[int] = []
    for user_id in ordered_ids:
        member = guild.get_member(user_id)
        if member is not None:
            resolved[user_id] = member
            continue
        unresolved_ids.append(user_id)

    if unresolved_ids:
        semaphore = asyncio.Semaphore(concurrency)

        async def fetch_member(user_id: int) -> tuple[int, discord.Member | None]:
            async with semaphore:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.NotFound:
                    member = None
                except discord.HTTPException as exc:
                    if raise_on_http_error:
                        raise MemberResolutionError(str(user_id)) from exc
                    member = None
            return user_id, member

        fetched = await asyncio.gather(*(fetch_member(user_id) for user_id in unresolved_ids))
        for user_id, member in fetched:
            if member is not None:
                resolved[user_id] = member

    return [
        (user_id, resolved[user_id]) for user_id in ordered_ids if user_id in resolved
    ]
