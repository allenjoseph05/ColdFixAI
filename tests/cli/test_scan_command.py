"""S-28.4 — `coldfix scan` refuses rather than prompts. ADR 184.

Nothing here opens a container, a database or a connection: the refusals happen
before any of that, and the assembly is a pure function precisely so it can be
checked without them. What is under test is what the command declines to do, and
what it hands the seven nodes when it does not decline.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue

from coldfix.cli import scan
from coldfix.cli.config import ConfigError
from coldfix.cli.main import main
from coldfix.cli.scan import (
    CHECKPOINTS,
    Journal,
    ScanConfig,
    ScanRefusedError,
    load_scan,
    open_journal,
    plan_scan,
    resources_for,
    run_id_for,
    run_scan,
    source_reader,
)
from coldfix.collect.workspace import PathEscapesWorkspaceError
from coldfix.cost.accounting import TokenUsage
from coldfix.evidence.adversary import PatchReview, Verdict
from coldfix.evidence.revisions import Comparison, Side
from coldfix.llm.client import ModelResponse
from coldfix.pipeline.graph import Node, build
from coldfix.pipeline.nodes import Resources, bind
from coldfix.sandbox.production import ProductionGuardError
from coldfix.state.persistent import Collection, Entry, PersistentStore

JOURNAL_URL = "postgresql://coldfix:hunter2@localhost/coldfix_memory"

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


class Forgetful:
    """An empty `Remembers`. Nothing here runs a search, so nothing is recorded."""

    def remember(self, finding: str, entry: Mapping[str, JsonValue]) -> None:
        del finding, entry

    def recalled(self, finding: str) -> Sequence[Mapping[str, JsonValue]]:
        del finding
        return ()


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
    assert resources.store is None, "a run without a journal declares none"


# --------------------------------------------------------------- the journal


def journalled(tmp_path: Path) -> ScanConfig:
    body = COMPLETE.replace(
        'image = "subject:latest"', f'image = "subject:latest"\njournal_url = "{JOURNAL_URL}"'
    )
    return load_scan(written(tmp_path, body))


def test_a_run_may_declare_a_journal_and_a_run_without_one_still_runs(tmp_path: Path) -> None:
    """Optional on purpose: what a run without one loses is memory across a
    rewind, not the ability to answer."""
    assert config_of(tmp_path).journal_url is None
    assert journalled(tmp_path).journal_url == JOURNAL_URL


def test_the_plan_says_whether_a_rewind_will_cost_the_run_its_memory(tmp_path: Path) -> None:
    """Discovered in `plan` rather than by paying twice for one measurement."""
    assert "re-measure candidates that already lost" in "\n".join(plan_scan(config_of(tmp_path)))
    assert "a journal outlives a rewind" in "\n".join(plan_scan(journalled(tmp_path)))


def test_the_plan_never_prints_the_journal_credential(tmp_path: Path) -> None:
    """A plan is the output most likely to be pasted into a message."""
    assert "hunter2" not in "\n".join(plan_scan(journalled(tmp_path)))


def test_a_journal_the_run_declares_reaches_the_nodes(tmp_path: Path) -> None:
    """The seam is only worth having if the composition root binds it: `optimize`
    reads `resources.store`, and nothing else in the run can supply it."""
    workspace = tmp_path / "worktree"
    workspace.mkdir()
    resources = resources_for(
        config_of(tmp_path),
        client=Silent(),
        counter=Silent(),
        workspace=workspace,
        store=Forgetful(),
    )
    assert resources.store is not None


class Recording:
    """Stands in for the Postgres half of the journal.

    What needs a database is the append-only trigger, and its tests skip without
    Docker. What needs none is the adapter's own decision: which collection it
    writes to, and whether it keys by finding.
    """

    def __init__(self) -> None:
        self.appended: list[tuple[Collection, str]] = []
        self.asked: list[tuple[Collection, str | None]] = []
        self.rows: list[Entry] = []

    def append(self, collection: Collection, key: str, entry: Mapping[str, JsonValue]) -> Entry:
        self.appended.append((collection, key))
        row = Entry(
            id=len(self.rows) + 1,
            collection=collection,
            key=key,
            entry=entry,
            written_at=datetime(2026, 9, 12, tzinfo=UTC),
        )
        self.rows.append(row)
        return row

    def read(self, collection: Collection, key: str | None = None) -> Sequence[Entry]:
        self.asked.append((collection, key))
        return tuple(row for row in self.rows if key is None or row.key == key)


def test_the_adapter_writes_failure_memory_and_keys_it_by_the_finding() -> None:
    """A bug here hands one finding another finding's failures, which looks like
    a search that mysteriously refuses to try the obvious fix."""
    recording = Recording()
    journal = Journal(store=cast("PersistentStore", recording))

    journal.remember("f-1", {"candidate": {"approach": "prefetch"}})
    journal.remember("f-2", {"candidate": {"approach": "an index"}})

    assert recording.appended == [
        (Collection.FAILURE_MEMORY, "f-1"),
        (Collection.FAILURE_MEMORY, "f-2"),
    ]
    assert [entry["candidate"] for entry in journal.recalled("f-1")] == [{"approach": "prefetch"}]
    assert recording.asked == [(Collection.FAILURE_MEMORY, "f-1")], "it asked for one finding"


class FakeWorktree:
    def __init__(self, path: Path) -> None:
        self.path = path


class FakeRepository:
    """A `Repository` that creates a directory instead of a git worktree."""

    def __init__(self, root: Path) -> None:
        del root

    def create_worktree(self, path: Path, revision: str) -> FakeWorktree:
        del revision
        path.mkdir(parents=True, exist_ok=True)
        return FakeWorktree(path)


class NoCheckpointer:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exception: object) -> None:
        return None


class FakeGraph:
    """A compiled graph that reports a route without running a node."""

    def invoke(self, state: object, config: object) -> dict[str, str]:
        del state, config
        return {"route": "done"}


def test_the_journal_the_run_opened_is_the_one_the_nodes_are_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one line in `run_scan` no direct test reaches.

    Found by the sabotage pass: with `store=None` written there, the journal is
    opened, the plan still says memory is on, and every other test in this file
    passes -- while no node can reach it. The four things the run opens are faked
    because none of them is what is under test here.
    """
    journal = Forgetful()
    given: dict[str, Any] = {}

    def recording(config: ScanConfig, **passed: Any) -> Resources:
        given.update(passed)
        return resources_for(config, **passed)

    monkeypatch.setattr(scan, "open_journal", lambda config: journal)
    monkeypatch.setattr(scan, "connect", lambda credential: Silent())
    monkeypatch.setattr(scan, "Repository", FakeRepository)
    monkeypatch.setattr(scan, "for_development", lambda path: NoCheckpointer())
    monkeypatch.setattr(scan, "build", lambda wiring, **options: FakeGraph())
    monkeypatch.setattr(scan, "resources_for", recording)

    lines = run_scan(journalled(tmp_path), spend=True, credential="sk-test")

    assert given["store"] is journal, "the run opened a journal the nodes never got"
    assert any("done" in line for line in lines)


def parked_state() -> dict[str, Any]:
    """A run stopped in front of a person, as the graph hands it back."""
    unchanged = Side(
        ran=True, output_digest="d", output_bytes=1, wall_s=1.0, peak_rss_bytes=1, stable=True
    )
    audited = PatchReview(
        verdict=Verdict.CLEAN,
        comparisons=(Comparison(given=("one",), baseline=unchanged, patched=unchanged),),
        turns=2,
    )
    return {
        "route": "clean",
        "findings": {
            "f-1": {
                "claim": {
                    "kind": "repeated_query",
                    "summary": "161 queries for 40 rows",
                    "location": {"file": "app.py", "line": 12, "symbol": "books"},
                    "evidence": [{"measurement_id": "m-1", "field": "wall.median", "value": 2.41}],
                    "basis": "ablation",
                    "payoff": 0.78,
                    "proof": {
                        "measurement_id": "m-1",
                        "before": 2.41,
                        "after": 0.53,
                        "share_removed": 0.78,
                    },
                },
                "attested_against": ["m-1"],
            }
        },
        "coverage": {"grounding": "measured"},
        "repaired": {
            "finding": "f-1",
            "approach": "prefetch the authors",
            "diff": "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-slow()\n+fast()\n",
            "test": "def test_one_query(): assert queries() == 1",
            "measurement_id": "m-c1",
            "share_removed": 0.384,
            "trades": [],
        },
        "audited": audited.model_dump(mode="json"),
    }


def test_a_parked_run_prints_the_report_rather_than_a_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate is the whole reason the run stops here. Found by the same sabotage
    shape twice already: with the render dropped, `run_scan` still reports a
    route, every direct test of the report still passes, and the person the run
    parked in front of is shown nothing."""

    class Parked:
        def invoke(self, state: object, config: object) -> dict[str, Any]:
            del state, config
            return parked_state()

    monkeypatch.setattr(scan, "open_journal", lambda config: None)
    monkeypatch.setattr(scan, "connect", lambda credential: Silent())
    monkeypatch.setattr(scan, "Repository", FakeRepository)
    monkeypatch.setattr(scan, "for_development", lambda path: NoCheckpointer())
    monkeypatch.setattr(scan, "build", lambda wiring, **options: Parked())

    printed = "\n".join(run_scan(config_of(tmp_path), spend=True, credential="sk-test"))

    assert "READY TO SHIP — f-1: prefetch the authors" in printed
    assert "PROVEN" in printed and "SUSPECTED" in printed
    assert "+fast()" in printed


def test_a_finished_run_prints_no_report_because_there_is_nothing_parked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ship` clears the handover, so a completed run reaches the same code path.
    It must not raise, and it must not invent a report."""
    monkeypatch.setattr(scan, "open_journal", lambda config: None)
    monkeypatch.setattr(scan, "connect", lambda credential: Silent())
    monkeypatch.setattr(scan, "Repository", FakeRepository)
    monkeypatch.setattr(scan, "for_development", lambda path: NoCheckpointer())
    monkeypatch.setattr(scan, "build", lambda wiring, **options: FakeGraph())

    printed = "\n".join(run_scan(config_of(tmp_path), spend=True, credential="sk-test"))

    assert "route        done" in printed
    assert "READY TO SHIP" not in printed


def test_no_journal_declared_opens_nothing(tmp_path: Path) -> None:
    assert open_journal(config_of(tmp_path)) is None


def test_a_journal_pointed_at_production_is_refused_before_it_is_opened(tmp_path: Path) -> None:
    """The guard is the constructor, so the refusal happens while there is still
    no connection, no worktree and no container."""
    body = COMPLETE.replace(
        'image = "subject:latest"',
        'image = "subject:latest"\njournal_url = "postgresql://app@prod-db/customers"',
    )
    with pytest.raises(ProductionGuardError):
        open_journal(load_scan(written(tmp_path, body)))


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
