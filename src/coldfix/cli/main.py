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
from collections.abc import Sequence
from pathlib import Path

from coldfix.cli.config import Config, ConfigError, load
from coldfix.cli.wiring import WiringError, adapter_for, supplied_by
from coldfix.explorer.registry import groundable, registered

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


def run(config: Config, *, spend: bool, credential: str | None) -> list[str]:
    """Start a real investigation.

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

    # **Deliberately not implemented here yet, and saying so is the honest state.**
    # Everything above this line is what makes the run one command; what is below
    # it is S-17.1, which is a run against the holdout repository that has never
    # happened. Wiring an unrun path and calling it done is how a system arrives
    # at its first real invocation with the confidence of code nobody has
    # executed — which is the failure this project keeps recording.
    message = (
        "assembling a live campaign is S-17.1 and has never been executed. `coldfix plan` "
        "reports what this configuration would supply; the remaining step is to hand those "
        "values to `campaign_for` and drive the graph, under the ceiling this file sets"
    )
    raise CommandError(message)


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
