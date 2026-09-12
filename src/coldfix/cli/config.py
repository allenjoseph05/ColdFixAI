"""Reading a configuration file, and refusing one that cannot describe a run.

**S-17.18, cut to v3 in S-31.1.** This module used to own `Config`: twenty-five
values that `campaign_for` could not be handed from a shell, most of them facts
about a Django project. v3 asks for a repository, an image and a budget, and
`cli/scan.py` reads them. What is left here is the part that was never v1's — the
error type, and the accessors that make a bad file say what to type.

**Every refusal names the section and the key.** A `KeyError` on `"image"` tells
somebody a dictionary lacked a key; *`[scan].image` is required* tells them what
to type. This file is the first thing a new user writes and the last thing they
want to debug, and the difference between those two messages is most of the
experience of it.

**Nothing here reads the environment or picks a default for a subject fact.**
S-7.2's convention — nothing under `src/` chooses an interpreter or a database on
its own account — applies with more force here than anywhere, because a default
in a config loader is invisible: the file looks complete and the run measures
something nobody asked for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """The configuration file could not be read, or does not describe a run."""


@dataclass(frozen=True)
class _Reader:
    """One accessor per shape, so every failure says the same kind of thing.

    A dataclass rather than a set of module functions because each accessor needs
    the whole document and the file's name, and threading both through every
    signature is how one of them ends up reporting a different path.

    Private by name and imported by `cli/scan.py`, which is the only caller left
    now that v1's loader is gone.
    """

    raw: dict[str, Any]
    path: Path

    def _section(self, section: str) -> dict[str, Any]:
        found = self.raw.get(section)
        if found is None:
            message = f"{self.path} has no [{section}] section"
            raise ConfigError(message)
        if not isinstance(found, dict):
            message = f"{self.path}: [{section}] must be a section, not a {_named(found)}"
            raise ConfigError(message)
        return found

    def _value(self, section: str, key: str) -> Any:  # noqa: ANN401 - a TOML value is
        # genuinely of unknown type until one of the accessors below has checked it;
        # that checking is this class's whole job and a narrower return would be a
        # claim made before the check.
        found = self._section(section).get(key)
        if found is None:
            message = f"{self.path}: [{section}].{key} is required and is not set"
            raise ConfigError(message)
        return found

    def _refuse(self, section: str, key: str, wanted: str, got: object) -> ConfigError:
        return ConfigError(
            f"{self.path}: [{section}].{key} must be {wanted}, got a {_named(got)} ({got!r})"
        )

    def text(self, section: str, key: str) -> str:
        found = self._value(section, key)
        if not isinstance(found, str) or not found.strip():
            raise self._refuse(section, key, "a non-empty string", found)
        return found

    def folder(self, section: str, key: str) -> Path:
        """Named `folder` rather than `path` because this reader already has a
        `path` — the file it is reading — and one of the two would shadow the
        other.

        **Not checked for existence.** A worktree root is created by the run, and
        refusing a path that is not there yet would refuse every first run.
        """
        return Path(self.text(section, key))

    def money(self, section: str, key: str, *, required: bool) -> Decimal | None:
        found = self._section(section).get(key)
        if found is None:
            if required:
                message = f"{self.path}: [{section}].{key} is required and is not set"
                raise ConfigError(message)
            return None
        # **A string, not a float.** A euro ceiling parsed from a float is a
        # ceiling that is very slightly not the number that was written, and the
        # one place that matters is the comparison that stops a run.
        if not isinstance(found, str):
            raise self._refuse(section, key, 'a quoted decimal such as "25.00"', found)
        try:
            return Decimal(found)
        except InvalidOperation as error:
            raise self._refuse(section, key, "a decimal number", found) from error

    def day(self, section: str, key: str) -> date:
        found = self._value(section, key)
        if isinstance(found, date):
            return found
        if isinstance(found, str):
            try:
                return date.fromisoformat(found)
            except ValueError as error:
                raise self._refuse(section, key, "a date such as 2026-09-12", found) from error
        raise self._refuse(section, key, "a date such as 2026-09-12", found)


def _named(value: object) -> str:
    return type(value).__name__
