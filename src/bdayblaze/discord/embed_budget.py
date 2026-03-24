from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import discord

EMBED_TITLE_LIMIT = 256
EMBED_DESCRIPTION_LIMIT = 4096
EMBED_FIELD_NAME_LIMIT = 256
EMBED_FIELD_VALUE_LIMIT = 1024
EMBED_FOOTER_LIMIT = 2048
EMBED_AUTHOR_LIMIT = 256
EMBED_FIELD_LIMIT = 25
EMBED_TOTAL_LIMIT = 6000

ELLIPSIS = "..."
ZERO_WIDTH_SPACE = "\u200b"


def truncate_text(value: str | None, limit: int) -> str:
    if limit <= 0:
        return ""
    normalized = (value or "").strip()
    if not normalized:
        return ZERO_WIDTH_SPACE if limit > 0 else ""
    if len(normalized) <= limit:
        return normalized
    if limit <= len(ELLIPSIS):
        return normalized[:limit]
    return normalized[: limit - len(ELLIPSIS)] + ELLIPSIS


def code_block_snippet(
    value: str | None,
    *,
    limit: int = EMBED_FIELD_VALUE_LIMIT,
    language: str = "text",
) -> str:
    opener = f"```{language}\n"
    closer = "\n```"
    available = max(1, limit - len(opener) - len(closer))
    return f"{opener}{truncate_text(value, available)}{closer}"


def embed_text_length(embed: discord.Embed) -> int:
    total = len(embed.title or "") + len(embed.description or "")
    total += len(embed.footer.text) if embed.footer and embed.footer.text else 0
    total += len(embed.author.name) if embed.author and embed.author.name else 0
    for field in embed.fields:
        total += len(field.name or "") + len(field.value or "")
    return total


@dataclass(slots=True)
class BudgetedEmbed:
    embed: discord.Embed
    used_characters: int = 0

    @classmethod
    def create(
        cls,
        *,
        title: str | None = None,
        description: str | None = None,
        color: discord.Color | None = None,
        timestamp: datetime | None = None,
    ) -> BudgetedEmbed:
        embed = discord.Embed(color=color, timestamp=timestamp)
        budget = cls(embed=embed)
        if title:
            budget.set_title(title)
        if description:
            budget.set_description(description)
        return budget

    @property
    def remaining_characters(self) -> int:
        return max(0, EMBED_TOTAL_LIMIT - self.used_characters)

    @property
    def remaining_fields(self) -> int:
        return max(0, EMBED_FIELD_LIMIT - len(self.embed.fields))

    def set_title(self, value: str | None) -> None:
        fitted = self._fit_component(value, EMBED_TITLE_LIMIT)
        if fitted:
            self.embed.title = fitted
            self.used_characters += len(fitted)

    def set_description(self, value: str | None) -> None:
        fitted = self._fit_component(value, EMBED_DESCRIPTION_LIMIT)
        if fitted:
            self.embed.description = fitted
            self.used_characters += len(fitted)

    def set_author(self, value: str | None) -> None:
        fitted = self._fit_component(value, EMBED_AUTHOR_LIMIT)
        if fitted:
            self.embed.set_author(name=fitted)
            self.used_characters += len(fitted)

    def set_footer(self, value: str | None) -> None:
        fitted = self._fit_component(value, EMBED_FOOTER_LIMIT)
        if fitted:
            self.embed.set_footer(text=fitted)
            self.used_characters += len(fitted)

    def add_field(self, name: str, value: str, *, inline: bool = False) -> bool:
        if self.remaining_fields <= 0 or self.remaining_characters <= 0:
            return False
        fitted_name = truncate_text(name, min(EMBED_FIELD_NAME_LIMIT, self.remaining_characters))
        if not fitted_name:
            return False
        remaining_after_name = self.remaining_characters - len(fitted_name)
        if remaining_after_name <= 0:
            return False
        fitted_value = truncate_text(
            value,
            min(EMBED_FIELD_VALUE_LIMIT, remaining_after_name),
        )
        if not fitted_value:
            return False
        self.embed.add_field(name=fitted_name, value=fitted_value, inline=inline)
        self.used_characters += len(fitted_name) + len(fitted_value)
        return True

    def add_line_fields(
        self,
        name: str,
        lines: Iterable[str],
        *,
        inline: bool = False,
        continuation_name: str | None = None,
    ) -> int:
        added = 0
        pending = [line.strip() for line in lines if line.strip()]
        if not pending:
            self.add_field(name, "Nothing to show.", inline=inline)
            return 1
        current_chunk: list[str] = []
        base_name = name
        follow_name = continuation_name or f"{name} (cont.)"

        def flush(chunk_name: str) -> bool:
            nonlocal added, current_chunk
            if not current_chunk:
                return False
            joined = "\n".join(current_chunk)
            if not self.add_field(chunk_name, joined, inline=inline):
                return False
            current_chunk = []
            added += 1
            return True

        chunk_name = base_name
        while pending and self.remaining_fields > 0 and self.remaining_characters > 0:
            candidate = pending.pop(0)
            test_chunk = "\n".join([*current_chunk, candidate]) if current_chunk else candidate
            if len(test_chunk) <= min(EMBED_FIELD_VALUE_LIMIT, self.remaining_characters):
                current_chunk.append(candidate)
                continue
            if not flush(chunk_name):
                fitted_line = truncate_text(
                    candidate,
                    min(EMBED_FIELD_VALUE_LIMIT, self.remaining_characters),
                )
                if fitted_line and self.add_field(chunk_name, fitted_line, inline=inline):
                    added += 1
                return added
            chunk_name = follow_name
            current_chunk.append(candidate)
        if pending and current_chunk:
            overflow_note = f"...and {len(pending)} more."
            joined = "\n".join(current_chunk)
            if len(joined) + len("\n") + len(overflow_note) <= EMBED_FIELD_VALUE_LIMIT:
                current_chunk.append(overflow_note)
        flush(chunk_name)
        return added

    def build(self) -> discord.Embed:
        return self.embed

    def _fit_component(self, value: str | None, component_limit: int) -> str:
        return truncate_text(value, min(component_limit, self.remaining_characters))
