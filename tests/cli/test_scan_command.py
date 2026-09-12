"""S-28.4 — `coldfix scan` refuses rather than prompts. ADR 184.

Nothing here opens a container, a database or a connection: the refusals happen
before any of that, and the assembly is a pure function precisely so it can be
checked without them. What is under test is what the command declines to do, and
what it hands the seven nodes when it does not decline.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from coldfix.cli.config import ConfigError
from coldfix.cli.main import main
from coldfix.cli.scan import (
    CHECKPOINTS,
    ScanConfig,
    ScanRefusedError,
    load_scan,
    plan_scan,
    resources_for,
    run_id_for,
    run_scan,
    source_reader,
)
from coldfix.collect.workspace import PathEscapesWorkspaceError
from coldfix.cost.accounting import TokenUsage
from coldfix.llm.client import ModelResponse
from coldfix.pipeline.graph import Node, build
from coldfix.pipeline.nodes import bind

COMPLETE = """
[scan]
root = "{root}"
revision = "HEAD"
image = "subject:latest"
worktree_root = "{worktrees}"

[budget]
ceiling_eur = "25.00"
rate_eur = "0.92"
rate_as_of = 2026-09-11
"""


class Silent:
    """A client that could answer and is never asked."""

    def complete(self, **kwargs: object) -> ModelResponse:
        return ModelResponse(
            model=str(kwargs["model"]),
            text="",
            usage=TokenUsage(input_tokens=0, output_tokens=0),
            stop_reason="end_turn",
        )

    def count_tokens(self, **kwargs: object) -> int:
        return 0


def written(tmp_path: Path, body: str = COMPLETE) -> Path:
    root = tmp_path / "subject"
    root.mkdir(exist_ok=True)
    path = tmp_path / "coldfix.toml"
    path.write_text(
        body.format(root=root.as_posix(), worktrees=(tmp_path / "worktrees").as_posix()),
        encoding="utf-8",
    )
    return path


def config_of(tmp_path: Path) -> ScanConfig:
    return load_scan(written(tmp_path))


# ------------------------------------------------------------ the configuration


def test_a_scan_needs_six_things_and_reads_them(tmp_path: Path) -> None:
    config = config_of(tmp_path)
    assert config.revision == "HEAD"
    assert config.image == "subject:latest"
    assert config.ceiling_eur == Decimal("25.00")
    assert config.rate_as_of == date(2026, 9, 11)
    assert config.database_url is None, "a subject without one declares none"


def test_a_scan_without_a_ceiling_is_refused_with_the_reason(tmp_path: Path) -> None:
    """`Budget` permits no ceiling as a development setting. A command whose
    purpose is to make paid calls is not that setting."""
    body = COMPLETE.replace('ceiling_eur = "25.00"\n', "")
    with pytest.raises(ConfigError, match="required for a scan"):
        load_scan(written(tmp_path, body))


def test_a_ceiling_written_as_a_number_is_refused(tmp_path: Path) -> None:
    """A euro ceiling parsed from a float is very slightly not the number that
    was written, and the one place that matters is the comparison that stops a
    run."""
    body = COMPLETE.replace('ceiling_eur = "25.00"', "ceiling_eur = 25.00")
    with pytest.raises(ConfigError, match="quoted decimal"):
        load_scan(written(tmp_path, body))


def test_a_configuration_missing_the_scan_section_says_so(tmp_path: Path) -> None:
    path = tmp_path / "coldfix.toml"
    path.write_text('[budget]\nceiling_eur = "1.00"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match=r"\[scan\]|rate_eur"):
        load_scan(path)


def test_the_run_id_does_not_move_between_invocations(tmp_path: Path) -> None:
    """An id carrying a timestamp makes every interrupted run unresumable while
    looking like it merely started again."""
    config = config_of(tmp_path)
    assert run_id_for(config) == run_id_for(config) == "subject@HEAD"


# ------------------------------------------------------------------ refusals


def test_a_scan_without_the_flag_refuses_and_opens_nothing(tmp_path: Path) -> None:
    """The flag is not a confirmation prompt. A prompt has a default."""
    with pytest.raises(ScanRefusedError, match="was not given --spend"):
        run_scan(config_of(tmp_path), spend=False, credential="sk-test")


def test_a_scan_with_the_flag_and_no_credential_refuses_before_opening_anything(
    tmp_path: Path,
) -> None:
    """Otherwise the run fails after a container is standing rather than before."""
    with pytest.raises(ScanRefusedError, match="ANTHROPIC_API_KEY is not set"):
        run_scan(config_of(tmp_path), spend=True, credential=None)


# --------------------------------------------------------------------- plan


def test_plan_says_what_a_run_would_be_given_and_spends_nothing(tmp_path: Path) -> None:
    lines = "\n".join(plan_scan(config_of(tmp_path)))
    assert "subject:latest" in lines
    assert "25.00 EUR" in lines
    assert CHECKPOINTS in lines
    assert "parks before `ship`" in lines


def test_plan_says_repair_is_not_available_yet(tmp_path: Path) -> None:
    """Discovered in `plan` rather than at the node, half an hour into a run."""
    assert "applying and measuring a candidate" in "\n".join(plan_scan(config_of(tmp_path)))


# ----------------------------------------------------------------- assembly


def test_what_the_nodes_are_given_carries_the_ceiling_and_the_image(tmp_path: Path) -> None:
    workspace = tmp_path / "worktree"
    workspace.mkdir()
    resources = resources_for(
        config_of(tmp_path), client=Silent(), counter=Silent(), workspace=workspace
    )
    assert resources.meter.budget.ceiling_eur == Decimal("25.00")
    assert resources.image == "subject:latest"
    assert resources.repairs is None, "nothing writes the failing test yet"
    assert build(bind(resources), gated=False) is not None
    assert set(bind(resources).steps()) == set(Node)


def test_the_command_reads_v3s_configuration_and_not_v1s(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`coldfix scan` through the entry point, which is how anybody runs it.

    v1's loader wants twenty-five values -- a settings module, an entity, a cost
    claim -- and would refuse this file for the absence of every one of them. The
    sabotage pass found this missing: with the dispatch disabled, every direct
    test still passed while the command itself was broken.
    """
    assert main(["--config", str(written(tmp_path)), "scan", "--plan"]) == 0
    printed = capsys.readouterr().out
    assert "subject:latest" in printed
    assert "25.00 EUR" in printed
    assert "parks before `ship`" in printed


def test_the_command_refuses_without_the_flag_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(written(tmp_path)), "scan"]) == 1
    assert "was not given --spend" in capsys.readouterr().out


def test_the_source_reader_refuses_a_path_that_leaves_the_workspace(tmp_path: Path) -> None:
    """By resolution, symlinks and all -- not by looking for `..` in a string."""
    workspace = tmp_path / "worktree"
    workspace.mkdir()
    (workspace / "app.py").write_text("x = 1\n", encoding="utf-8")
    read = source_reader(workspace)
    assert read("app.py") == "x = 1\n"
    with pytest.raises(PathEscapesWorkspaceError):
        read("../secrets.txt")
