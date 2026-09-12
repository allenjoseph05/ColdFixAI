"""`coldfix scan` -- v3's entry point. **S-28.4, ADR 184.**

`pipeline/nodes.py` builds the seven steps and `bind` wires them; nothing
constructed the `Resources` they close over. That assembly is a command: read a
configuration, refuse what must not run, open a checkpointer, invoke the graph,
say where the run went.

**Three refusals, and every one is a refusal rather than a prompt.** No
`--spend`, no ceiling, no credential. A prompt has a default and a default is
answered by habit; these have to be typed on purpose, and none of them is
reached after a container is standing.

**A ceiling is required here, unlike v1.** `Budget` accepts `ceiling_eur=None`
and documents it as a legitimate development setting -- which it is, for a test
that never calls a model. For a command whose whole purpose is to make paid
calls it is the setting that lets a loop spend without limit, so this one
refuses without it.

**v3 reads its own configuration.** v1's `Config` carries twenty-five values --
`settings`, `entity`, `model`, `suite_command`, a cost claim with guards -- and
almost all of it is the framework knowledge v3 exists to remove. A run here is a
repository, an image and a budget.

**This has never been executed against a real subject.** Every piece below is
covered by a test and no test runs all of them in one process with a live
client, because that costs money. Read `run_scan` as verified parts in an order
nobody has walked.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from pydantic import JsonValue

from coldfix.agent.toolbox import SandboxedToolbox
from coldfix.cli.config import ConfigError, _Reader
from coldfix.collect._docker_cli import DockerCli
from coldfix.collect.workspace import Workspace
from coldfix.cost.accounting import ExchangeRate
from coldfix.cost.accounting import Ledger as Bill
from coldfix.cost.budget import Budget
from coldfix.cost.routing import Router
from coldfix.evidence.ledger import Ledger
from coldfix.evidence.memory import Remembers
from coldfix.llm.client import ModelClient, connect
from coldfix.llm.metered import Meter, TokenCounter
from coldfix.orchestrator.checkpointing import for_development, thread
from coldfix.pipeline.graph import build
from coldfix.pipeline.nodes import Resources, bind
from coldfix.pipeline.report import awaiting_review, report_for
from coldfix.pipeline.state import PipelineState
from coldfix.sandbox.production import VerifiedDatabase
from coldfix.sandbox.runner import Sandbox
from coldfix.sandbox.worktrees import Repository
from coldfix.state.persistent import Collection, PersistentStore, refuse_shared_store

CHECKPOINTS = "checkpoints.sqlite"


class ScanRefusedError(Exception):
    """The scan will not start. Reported as a message and a non-zero exit.

    Defined here rather than reusing `main.CommandError` because `main` imports
    this module, and the other direction would be a cycle.
    """


@dataclass(frozen=True)
class ScanConfig:
    """What a v3 run needs. A repository, an image, and a budget."""

    root: Path
    revision: str
    image: str
    worktree_root: Path
    ceiling_eur: Decimal
    rate_eur: Decimal
    rate_as_of: date
    database_url: str | None = None
    """Only when the subject has one. `refuse` puts it through the production
    guard; absent, there is nothing to guard."""

    journal_url: str | None = None
    """ColdFix's own append-only journal -- **not the subject's database.**

    Optional because a run without one still answers; what it loses is memory
    across a rewind (ADR 186), and `--plan` says so rather than leaving the
    absence to be discovered by paying for the same measurement twice.
    """


def load_scan(path: Path) -> ScanConfig:
    """Read one `coldfix.toml`'s `[scan]` and `[budget]`.

    Raises:
        ConfigError: the file is missing, is not valid TOML, or does not carry
            what a scan needs -- including a ceiling, which is required.
    """
    path = Path(path)
    if not path.is_file():
        message = f"no configuration at {path}"
        raise ConfigError(message)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        message = f"{path} is not valid TOML: {error}"
        raise ConfigError(message) from error

    read = _Reader(raw, path)
    ceiling = read.money("budget", "ceiling_eur", required=False)
    if ceiling is None:
        message = (
            f"{path}: [budget].ceiling_eur is required for a scan and is not set. Without one "
            "the run has no global limit -- the per-phase caps still apply, and they bound the "
            "number of steps rather than what they cost. Set it to what this scan may spend"
        )
        raise ConfigError(message)

    database = raw.get("scan", {}).get("database_url")
    journal = raw.get("scan", {}).get("journal_url")
    return ScanConfig(
        root=read.folder("scan", "root"),
        revision=read.text("scan", "revision"),
        image=read.text("scan", "image"),
        worktree_root=read.folder("scan", "worktree_root"),
        ceiling_eur=ceiling,
        rate_eur=read.money("budget", "rate_eur", required=True) or Decimal(0),
        rate_as_of=read.day("budget", "rate_as_of"),
        database_url=read.text("scan", "database_url") if database is not None else None,
        journal_url=read.text("scan", "journal_url") if journal is not None else None,
    )


def run_id_for(config: ScanConfig) -> str:
    """The thread this run checkpoints under.

    Stable across invocations on purpose: an id carrying a timestamp would make
    every interrupted run unresumable while looking like it had merely started
    again.

    **Resolved before the name is taken.** `Path(".").name` is the empty string,
    and `root = "."` is the natural thing to write — so an unresolved root gives
    every such run the id `@HEAD`, and two different subjects configured the same
    way would share a checkpoint thread and resume into each other's state. Found
    by planning the shipped example after S-31.1 (ADR 190).
    """
    return f"{config.root.resolve().name}@{config.revision}"


@dataclass(frozen=True)
class Journal:
    """The rewind-proof store, as the pipeline's `Remembers`. **S-29.1, ADR 186.**

    The adapter lives here rather than in `evidence/` because `state.persistent`
    imports a database driver at module scope, and the package that runs inside a
    container must stay importable with nothing installed. Bound to
    `FAILURE_MEMORY`, which needs no new collection: *what was tried and did not
    work, keyed per finding* is exactly what a losing candidate is.
    """

    store: PersistentStore

    def remember(self, finding: str, entry: Mapping[str, JsonValue]) -> None:
        self.store.append(Collection.FAILURE_MEMORY, key=finding, entry=entry)

    def recalled(self, finding: str) -> Sequence[Mapping[str, JsonValue]]:
        return tuple(item.entry for item in self.store.read(Collection.FAILURE_MEMORY, finding))


def open_journal(config: ScanConfig) -> Journal | None:
    """Open the run's failure memory, or report that it has none.

    Opened before the worktree in `run_scan`, so a URL the production guard
    refuses stops the run while there is nothing to clean up. `initialize` is
    idempotent, so a run against an existing journal is not a special case.

    Raises:
        ProductionGuardError: the URL is not one this system may write to.
        SharedStoreError: it names the checkpoint database, which ADR 003 keeps
            separate so that dropping checkpoints cannot destroy what a rewind
            must not discard.
    """
    if config.journal_url is None:
        return None
    database = VerifiedDatabase(config.journal_url)
    refuse_shared_store(database, config.worktree_root / CHECKPOINTS)
    store = PersistentStore(database=database, replay_root=config.worktree_root / "replay")
    store.initialize()
    return Journal(store)


def source_reader(workspace: Path) -> Callable[[str], str]:
    """Read one file from the workspace, refusing a path that leaves it.

    Through `Workspace.locate`, so the refusal is by resolution -- symlinks and
    all -- rather than by inspecting the string for `..`.
    """
    space = Workspace(workspace)

    def read(path: str) -> str:
        return space.locate(path).read_text(encoding="utf-8")

    return read


def resources_for(
    config: ScanConfig,
    *,
    client: ModelClient,
    counter: TokenCounter,
    workspace: Path,
    store: Remembers | None = None,
) -> Resources:
    """Everything the seven nodes share. **Opens nothing.**

    Kept apart from `run_scan` because this is the half that can be tested: no
    container, no database, no key. What is left there is three constructors and
    an invoke, which is as small as the untested surface can be made.

    `repairs` is deliberately unset. Until the test-writing story lands there is
    no way to mint a `Falsified`, and a run that reaches `optimize` raises rather
    than reporting a search that never happened as one that found nothing.
    """
    measurements = Ledger()
    meter = Meter(
        client=client,
        counter=counter,
        router=Router(),
        budget=Budget(
            ledger=Bill(),
            rate=ExchangeRate(euros_per_dollar=config.rate_eur, as_of=config.rate_as_of),
            ceiling_eur=config.ceiling_eur,
        ),
    )
    return Resources(
        meter=meter,
        ledger=measurements,
        # A factory: the image is `refuse`'s to establish, not this function's to
        # assume (S-28.1b, ADR 191). Still opens nothing -- no container is
        # started until a node asks for tools.
        toolbox=lambda image: SandboxedToolbox(
            sandbox=Sandbox(image=image, workspace=workspace), ledger=measurements
        ),
        repository=workspace,
        image=config.image,
        docker=DockerCli(),
        read_source=source_reader(workspace),
        database_url=config.database_url,
        store=store,
    )


def plan_scan(config: ScanConfig) -> list[str]:
    """What a scan would be given. **Spends nothing and opens nothing.**"""
    return [
        f"root         {config.root} @ {config.revision}",
        f"image        {config.image}",
        f"ceiling      {config.ceiling_eur} EUR at {config.rate_eur} EUR/$ "
        f"(as of {config.rate_as_of.isoformat()})",
        f"database     {config.database_url or 'none declared'}",
        f"run          {run_id_for(config)}",
        # Named, never printed: the URL carries a password, and a plan is the
        # output most likely to be pasted into a message.
        "memory       "
        + (
            "a journal outlives a rewind"
            if config.journal_url
            else "none declared -- a rewind will re-measure candidates that already lost"
        ),
        f"checkpoints  {config.worktree_root / CHECKPOINTS}",
        "gate         the run parks before `ship`; nothing reaches a repository unseen",
        "repair       unavailable: applying and measuring a candidate needs a worktree "
        "and a container this command does not open yet, so a run that proves a finding "
        "will stop at `optimize` rather than pretend a search happened",
    ]


def run_scan(config: ScanConfig, *, spend: bool, credential: str | None) -> list[str]:
    """Start a real scan.

    Raises:
        ScanRefusedError: `--spend` was not given, or no credential is set. Both are
            refusals rather than prompts, and both happen before anything opens.
    """
    if not spend:
        message = (
            "`coldfix scan` makes paid model calls and was not given --spend. Nothing has been "
            "opened and nothing has been billed. `coldfix scan --plan` answers whether the "
            "configuration is right without spending anything"
        )
        raise ScanRefusedError(message)
    if not credential:
        message = (
            "--spend was given and ANTHROPIC_API_KEY is not set, so the run would fail after "
            "standing up a container rather than before. Set it, or use `coldfix scan --plan`"
        )
        raise ScanRefusedError(message)

    # Built first, so an unusable key is refused while nothing is open. `connect`
    # opens no connection; the first completion is what bills.
    client = connect(credential)
    # Before the worktree, so a journal the guard refuses stops the run while
    # there is still nothing to clean up.
    journal = open_journal(config)

    run_id = run_id_for(config)
    worktree = Repository(root=config.root).create_worktree(
        config.worktree_root / run_id, config.revision
    )
    # Not destroyed here: the run parks at the ship gate, and a resumed run needs
    # the workspace its measurements were taken in.
    resources = resources_for(
        config, client=client, counter=client, workspace=worktree.path, store=journal
    )

    with for_development(config.worktree_root / CHECKPOINTS) as checkpointer:
        graph = build(bind(resources), checkpointer=checkpointer, gated=True)
        final = graph.invoke(PipelineState(), thread(run_id))

    lines = [
        f"run          {run_id}",
        f"workspace    {worktree.path}",
        f"checkpoints  {config.worktree_root / CHECKPOINTS}",
        f"route        {final.get('route', 'none written')}",
    ]
    # The run parks before `ship` and this is the person it parks in front of.
    # Asked through `awaiting_review` rather than by testing `repaired` here, so
    # what counts as being at the gate has one owner (ADR 188).
    parked = PipelineState.model_validate(final)
    if awaiting_review(parked):
        lines.extend(["", *report_for(parked).lines()])
    return lines
