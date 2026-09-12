"""S-28.1b — the wheel the container installs. ADR 191.

Every refusal here passes `out_dir=`, and that is deliberate rather than
incidental: `build_wheel` consults its per-process cache *before* it looks for
`uv`, so a test that had already built one would make the "uv is missing" case
return a cached path instead of raising. Passing an explicit directory bypasses
the cache, so these tests cannot depend on what ran before them.

The stdlib names are patched directly rather than through
`coldfix.collect.wheel.shutil`. Reaching through a module to something it
imported is an implicit re-export -- which strict typing refuses, and which reads
as though the attribute belonged to the module doing the importing.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from coldfix.collect.wheel import WheelError, build_wheel, project_root


class Completed:
    """What `subprocess.run` returns, for a build that did not really happen."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = stderr


def test_the_project_root_is_the_directory_holding_pyproject() -> None:
    """Walked from this package rather than configured: the package may be
    installed anywhere, and a setting is one more thing that can point at the
    wrong tree."""
    root = project_root()

    assert (root / "pyproject.toml").is_file()
    assert (root / "src" / "coldfix").is_dir()


def test_uv_missing_is_reported_as_a_fault_of_this_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not as something about the image. The subject has no say in whether the
    machine running ColdFix can build a wheel, and a message that blamed the
    image would send somebody to read a Dockerfile."""
    monkeypatch.setattr("shutil.which", lambda _name: None)

    with pytest.raises(WheelError, match="requirement of the machine running ColdFix"):
        build_wheel(out_dir=tmp_path)


def test_a_build_that_fails_says_so_rather_than_returning_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "uv")
    monkeypatch.setattr("subprocess.run", lambda *_a, **_k: Completed(1, "no build backend"))

    with pytest.raises(WheelError, match="no build backend"):
        build_wheel(out_dir=tmp_path)


def test_a_stale_wheel_beside_a_fresh_one_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure this guard exists for is silent: two wheels in a directory and
    the container installs whichever sorts first, so it runs a collector that is
    not this source while every field name still matches."""
    monkeypatch.setattr("shutil.which", lambda _name: "uv")
    monkeypatch.setattr("subprocess.run", lambda *_a, **_k: Completed())
    (tmp_path / "coldfix-0.1.0-py3-none-any.whl").write_bytes(b"")
    (tmp_path / "coldfix-0.0.9-py3-none-any.whl").write_bytes(b"")

    with pytest.raises(WheelError, match="exactly one"):
        build_wheel(out_dir=tmp_path)


def test_a_build_that_produced_nothing_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`uv` exiting zero having written no wheel would otherwise surface as an
    `IndexError` three frames away from the cause."""
    monkeypatch.setattr("shutil.which", lambda _name: "uv")
    monkeypatch.setattr("subprocess.run", lambda *_a, **_k: Completed())

    with pytest.raises(WheelError, match="exactly one"):
        build_wheel(out_dir=tmp_path)


def test_the_build_is_asked_for_a_wheel_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source distribution would not install into the image, and building one
    is time the scan spends for nothing."""
    asked: list[list[str]] = []

    def record(argv: list[str], **_: object) -> Completed:
        asked.append(argv)
        (tmp_path / "coldfix-0.1.0-py3-none-any.whl").write_bytes(b"")
        return Completed()

    monkeypatch.setattr("shutil.which", lambda _name: "uv")
    monkeypatch.setattr("subprocess.run", record)

    built = build_wheel(out_dir=tmp_path)

    assert built.name.endswith(".whl")
    assert asked[0][:3] == ["uv", "build", "--wheel"]


@pytest.mark.slow
def test_a_real_build_produces_a_wheel_carrying_the_collector() -> None:
    """The whole story rests on this: what the container installs has to contain
    the module the container runs."""
    built = build_wheel()

    assert built.is_file()
    assert "coldfix/collect/run.py" in zipfile.ZipFile(built).namelist()
    assert build_wheel() == built, "built once per process, not once per caller"
