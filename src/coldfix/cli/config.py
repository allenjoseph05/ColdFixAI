"""The file that supplies what `campaign_for` cannot be handed from a shell.

**S-17.18.** `campaign_for` takes twenty-five required arguments. Fourteen are
strings, paths and numbers a file can hold; the other eleven are objects, and
four of those are the adapter's. This module owns the first fourteen and refuses
a file that cannot produce them.

**Every refusal names the section and the key.** A `KeyError` on `"database_url"`
tells somebody a dictionary lacked a key; *`[subject].database_url` is required*
tells them what to type. This file is the first thing a new user writes and the
last thing they want to debug, and the difference between those two messages is
most of the experience of it.

**Nothing here reads the environment or picks a default for a subject fact.**
S-7.2's convention — nothing under `src/` chooses an interpreter or a database on
its own account — applies with more force here than anywhere, because a default
in a config loader is invisible: the file looks complete and the run measures
something nobody asked for. The only defaults are for values that are genuinely
this tool's own and not the subject's.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """The configuration file could not be read, or does not describe a run."""


@dataclass(frozen=True)
class Claim:
    """S-10.1's cost claim, as a file states it."""

    metric: str
    baseline: float
    at_most: float
    guards: tuple[tuple[str, float, float], ...]


@dataclass(frozen=True)
class Config:
    """Everything a run needs that is not an object.

    Flat rather than nested, because the sections in the file are for the reader
    and the arguments to `campaign_for` are flat — a nested shape here would be a
    third arrangement of the same twenty-five values, to be kept in step with two
    others.
    """

    project: str
    root: Path
    revision: str
    framework: str
    trust_key: str

    python: tuple[str, ...]
    database_url: str
    settings: str
    source: str
    suite_command: tuple[str, ...]
    entity: str
    path: str
    model: str
    metric: str

    workload_id: str
    workload_description: str

    ceiling_eur: Decimal | None
    rate_eur: Decimal
    rate_as_of: date

    prefix_tokens: int
    prompt_tokens: int

    claim: Claim

    image: str
    worktree_root: Path
    store_url: str


def load(path: Path) -> Config:
    """Read one `coldfix.toml`.

    Raises:
        ConfigError: the file is missing, is not valid TOML, or does not carry
            every value a run needs. One message per problem, naming the section
            and the key.
    """
    path = Path(path)
    if not path.is_file():
        message = (
            f"no configuration at {path}. `coldfix.example.toml` at the repository root "
            "is a complete one to copy"
        )
        raise ConfigError(message)

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        message = f"{path} is not valid TOML: {error}"
        raise ConfigError(message) from error

    read = _Reader(raw, path)
    return Config(
        project=read.text("project", "name"),
        root=read.folder("project", "root"),
        revision=read.text("project", "revision"),
        framework=read.text("project", "framework"),
        trust_key=read.text("project", "trust_key"),
        python=read.words("subject", "python"),
        database_url=read.text("subject", "database_url"),
        settings=read.text("subject", "settings"),
        source=read.text("subject", "source"),
        suite_command=read.words("subject", "suite_command"),
        entity=read.text("subject", "entity"),
        path=read.text("subject", "path"),
        model=read.text("subject", "model"),
        metric=read.text("subject", "metric"),
        workload_id=read.text("workload", "id"),
        workload_description=read.text("workload", "description"),
        ceiling_eur=read.money("budget", "ceiling_eur", required=False),
        rate_eur=read.money("budget", "rate_eur", required=True) or Decimal(0),
        rate_as_of=read.day("budget", "rate_as_of"),
        prefix_tokens=read.count("tokens", "prefix"),
        prompt_tokens=read.count("tokens", "prompt"),
        claim=read.claim(),
        image=read.text("sandbox", "image"),
        worktree_root=read.folder("sandbox", "worktree_root"),
        store_url=read.text("store", "url"),
    )


@dataclass(frozen=True)
class _Reader:
    """One accessor per shape, so every failure says the same kind of thing.

    A dataclass rather than a set of module functions because each accessor needs
    the whole document and the file's name, and threading both through nine
    signatures is how one of them ends up reporting a different path.
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
        other."""
        return Path(self.text(section, key))

    def words(self, section: str, key: str) -> tuple[str, ...]:
        found = self._value(section, key)
        if not isinstance(found, list) or not found or not all(isinstance(x, str) for x in found):
            raise self._refuse(section, key, "a non-empty list of strings", found)
        return tuple(found)

    def count(self, section: str, key: str) -> int:
        found = self._value(section, key)
        if not isinstance(found, int) or isinstance(found, bool) or found < 0:
            raise self._refuse(section, key, "a non-negative whole number", found)
        return found

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
                raise self._refuse(section, key, "a date such as 2026-08-30", found) from error
        raise self._refuse(section, key, "a date such as 2026-08-30", found)

    def claim(self) -> Claim:
        guards: list[tuple[str, float, float]] = []
        for index, entry in enumerate(self._section("claim").get("guards", [])):
            if not isinstance(entry, dict):
                raise self._refuse("claim", f"guards[{index}]", "a table", entry)
            missing = sorted({"metric", "baseline", "at_most"} - set(entry))
            if missing:
                message = f"{self.path}: [claim].guards[{index}] is missing {missing}"
                raise ConfigError(message)
            guards.append((str(entry["metric"]), float(entry["baseline"]), float(entry["at_most"])))
        return Claim(
            metric=self.text("claim", "metric"),
            baseline=self.number("claim", "baseline"),
            at_most=self.number("claim", "at_most"),
            guards=tuple(guards),
        )

    def number(self, section: str, key: str) -> float:
        found = self._value(section, key)
        if isinstance(found, bool) or not isinstance(found, int | float):
            raise self._refuse(section, key, "a number", found)
        return float(found)


def _named(value: object) -> str:
    return type(value).__name__
