"""`coldfix` — the command that starts a run. **S-17.18.**

The library was finished before anything could start it: no `console_scripts`, no
`__main__`, and `campaign_for` called from two files, both tests. So the only way
to run this system was to hand-assemble twenty-five arguments in Python, for the
first time, on the day the run costs money.

**`plan` is the default and it cannot spend anything.** It reads the file,
resolves the adapter, asks it for the four values it supplies, and reports what a
run would be given — without opening a container, a database, or a model client.
That is deliberately less than `campaign_for` does: opening a workbench needs
Docker and opening the store needs Postgres, and a command whose purpose is *have
I configured this correctly* should not require both to answer.

**`run` refuses unless it is told to spend.** A tool whose easiest invocation
costs money is one that will eventually cost money by mistake, and the run this
starts is the one `04-cost.md` §12.3 prices. The flag is not a confirmation
prompt: prompts get answered by habit, and this one has to be typed on purpose.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from coldfix.cli.config import Config, ConfigError, load
from coldfix.cli.wiring import WiringError, adapter_for, supplied_by
from coldfix.cost.accounting import ExchangeRate
from coldfix.explorer.compose import Plan
from coldfix.explorer.registry import groundable, registered
from coldfix.llm.client import ModelClient, connect
from coldfix.orchestrator.adapters import Tokens
from coldfix.orchestrator.assembly import campaign_for
from coldfix.orchestrator.campaign import gated_graph
from coldfix.orchestrator.checkpointing import for_development
from coldfix.orchestrator.resume import start
from coldfix.repair.falsification import CostClaim, Guard
from coldfix.sandbox.modes import Workbench
from coldfix.sandbox.production import VerifiedDatabase
from coldfix.sandbox.worktrees import Repository
from coldfix.state.persistent import PersistentStore

DEFAULT_CONFIG = Path("coldfix.toml")

CREDENTIAL = "ANTHROPIC_API_KEY"
"""Read here and nowhere under `src/` outside this module. A library that reached
for an environment variable would be a library that could start spending because
of something in a shell profile."""


class CommandError(Exception):
    """The command cannot proceed. Reported as a message and a non-zero exit."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coldfix",
        description="Find performance problems by running experiments.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"the run's configuration (default: {DEFAULT_CONFIG})",
    )
    commands = parser.add_subparsers(dest="command")

    commands.add_parser(
        "plan",
        help="check the configuration and report what a run would be given. Spends nothing.",
    )
    running = commands.add_parser("run", help="investigate the subject. Costs money.")
    running.add_argument(
        "--spend",
        action="store_true",
        help="permit this run to make paid model calls. Required; there is no prompt.",
    )
    return parser


def plan(config: Config) -> list[str]:
    """What a run would be given, as lines to print. **Makes no model call.**

    Returns the report rather than printing it, so a test can read it without
    capturing output — and so the same lines can later be written to a file
    without this function learning about files.
    """
    adapter = adapter_for(config.framework)
    supplied = supplied_by(adapter, root=config.root, python=config.python, path=config.path)

    lines = [
        f"project      {config.project} @ {config.revision}",
        f"root         {config.root}",
        f"framework    {config.framework}",
        f"groundable   {'yes' if groundable(config.framework) else 'no'}"
        f"   (registered: {', '.join(registered()) or 'none'})",
        f"adapter      {type(adapter).__name__}",
        f"workload     {config.workload_id} — {config.workload_description}",
        f"entry point  {config.path}",
        f"metric       {config.metric}",
        f"counters     {', '.join(supplied['counters'])}",  # type: ignore[arg-type]
        f"capabilities {len(supplied['capabilities'])} declared",  # type: ignore[arg-type]
        f"resets       {len(supplied['reset_candidates'])} candidate(s)",  # type: ignore[arg-type]
        f"database     {config.database_url}",
        f"store        {config.store_url}",
        f"ceiling      {'none' if config.ceiling_eur is None else f'{config.ceiling_eur} EUR'}",
    ]
    if not groundable(config.framework):
        lines.append(
            "\nthis framework has an adapter but nothing registered grounding support for it, "
            "so a run would be refused at the fingerprint"
        )
    return lines


def run_id_for(config: Config) -> str:
    """The thread a run checkpoints under.

    Stable across invocations on purpose: `resume` continues a thread and
    `start` opens one, and an id containing a timestamp would make every
    interrupted campaign unresumable while looking like it had merely started
    again.
    """
    return f"{config.project}@{config.revision}"


def campaign_arguments(
    config: Config,
    supplied: Mapping[str, object],
    *,
    client: ModelClient,
    workbench: Workbench,
    store: PersistentStore,
) -> dict[str, object]:
    """`Config` and an adapter, translated into what `campaign_for` takes.

    Kept separate from `run` because this is the half that can be tested: it
    opens nothing, so every field can be checked without Docker, without
    Postgres and without a key. What is left in `run` is three constructors and a
    call, which is as small as the untested surface can be made.

    **`target` is the model, `entity` is the route's entity.** They are different
    strings — `coldfix.example.toml` has `entity = "author"` against
    `model = "shop.Book"` — and `Plan` uses them for different things: `entity`
    breaks a tie between factories, `target` is what synthesis seeds. Passing the
    entity as the target seeds the wrong table, which `Plan`'s own docstring names
    as the failure that measures an empty list.
    """
    return {
        **supplied,
        "client": client,
        "project": config.project,
        "trust_key": config.trust_key,
        "revision": config.revision,
        "root": config.root,
        "python": config.python,
        "database_url": config.database_url,
        "workbench": workbench,
        "store": store,
        "plan": Plan(
            workload_id=config.workload_id,
            description=config.workload_description,
            entity=config.entity,
            target=config.model,
        ),
        "entity": config.entity,
        "path": config.path,
        "model": config.model,
        "settings": config.settings,
        "source": config.source,
        "suite_command": config.suite_command,
        "metric": config.metric,
        "tokens": Tokens(prefix=config.prefix_tokens, prompt=config.prompt_tokens),
        "claim": CostClaim(
            metric=config.claim.metric,
            baseline=config.claim.baseline,
            at_most=config.claim.at_most,
            guards=tuple(
                Guard(metric=metric, baseline=baseline, at_most=at_most)
                for metric, baseline, at_most in config.claim.guards
            ),
        ),
        "rate": ExchangeRate(euros_per_dollar=config.rate_eur, as_of=config.rate_as_of),
        "ceiling_eur": config.ceiling_eur,
    }


def run(config: Config, *, spend: bool, credential: str | None) -> list[str]:
    """Start a real investigation. **S-17.1.**

    **This has never been executed against a real subject.** Every piece below is
    covered by a test — the translation directly, the assembly by S-17.15, the
    compile by Epic 17's composition check, the invoke by S-12.2 — and no test
    has run all four in one process with a live client, because that costs money.
    Read the sequence as four verified steps in an order nobody has walked.

    Raises:
        CommandError: `--spend` was not given, or no credential is set. Both are
            refusals rather than prompts — see the module docstring.
    """
    if not spend:
        message = (
            "`coldfix run` makes paid model calls and was not given --spend. Nothing has been "
            "opened and nothing has been billed. `coldfix plan` answers whether the "
            "configuration is right without spending anything"
        )
        raise CommandError(message)
    if not credential:
        message = (
            f"--spend was given and {CREDENTIAL} is not set, so the run would fail after "
            "standing up a container and a database rather than before. Set it, or use "
            "`coldfix plan`"
        )
        raise CommandError(message)

    adapter = adapter_for(config.framework)
    supplied = supplied_by(adapter, root=config.root, python=config.python, path=config.path)

    # Built before the workbench and the store so an unusable key is refused
    # while nothing is open. `connect` opens no connection; the first `complete`
    # is what bills.
    client = connect(credential)

    workbench = Workbench(
        repository=Repository(root=config.root),
        image=config.image,
        worktree_root=config.worktree_root,
    )
    store = PersistentStore(
        database=VerifiedDatabase(config.store_url),
        # Derived rather than configured: the replay cache belongs beside the
        # worktrees it is keyed against, and a second setting for it would be one
        # more thing to get inconsistent with `worktree_root`.
        replay_root=config.worktree_root / "recordings",
    )

    run_id = run_id_for(config)
    arguments = campaign_arguments(
        config, supplied, client=client, workbench=workbench, store=store
    )

    with (
        campaign_for(**arguments) as resources,  # type: ignore[arg-type]
        for_development(config.worktree_root / "checkpoints.sqlite") as checkpointer,
    ):
        graph = gated_graph(resources, checkpointer)
        final = start(graph, run_id)

    return [
        f"run          {run_id}",
        f"checkpoints  {config.worktree_root / 'checkpoints.sqlite'}",
        f"channels     {', '.join(sorted(final)) or 'none written'}",
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns the process exit code rather than raising."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        return 2

    try:
        config = load(arguments.config)
        if arguments.command == "plan":
            report = plan(config)
        else:
            report = run(config, spend=arguments.spend, credential=os.environ.get(CREDENTIAL))
    except (ConfigError, WiringError, CommandError) as error:
        print(f"coldfix: {error}")
        return 1

    for line in report:
        print(line)
    return 0
