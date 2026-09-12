"""`coldfix` — the command that starts a scan. **S-17.18, cut to v3 in S-31.1.**

The library was finished before anything could start it: no `console_scripts`, no
`__main__`, and the only way to run this system was to hand-assemble arguments in
Python, for the first time, on the day the run costs money. This is the entry
point that fixed that, and S-31.1 cut it down to the one pipeline that remains.

**`scan` is the only command.** `plan` and `run` drove v1 — a Django-specific
grounding path through `explorer/` and `adapters/`, twenty-five configuration
values, and a `campaign_for` assembly that v3 replaced node for node. Both are
gone, and so is the `Config` they read. What is left reads `[scan]`: a
repository, an image and a budget.

**`--plan` cannot spend anything.** It reads the configuration and reports what a
run would be given, without opening a container, a database, or a model client.

**`scan` refuses unless it is told to spend.** A tool whose easiest invocation
costs money is one that will eventually cost money by mistake. The flag is not a
confirmation prompt: prompts get answered by habit, and this one has to be typed
on purpose.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from coldfix.cli.config import ConfigError
from coldfix.cli.scan import ScanRefusedError, load_scan, plan_scan, run_scan

DEFAULT_CONFIG = Path("coldfix.toml")

CREDENTIAL = "ANTHROPIC_API_KEY"
"""Read here and nowhere under `src/` outside this module. A library that reached
for an environment variable would be a library that could start spending because
of something in a shell profile."""


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

    scanning = commands.add_parser(
        "scan",
        help="find waste by running the subject, and propose fixes. Costs money.",
    )
    scanning.add_argument(
        "--spend",
        action="store_true",
        help="permit this scan to make paid model calls. Required; there is no prompt.",
    )
    scanning.add_argument(
        "--plan",
        action="store_true",
        help="report what a scan would be given and stop. Spends nothing.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns the process exit code rather than raising."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        return 2

    try:
        config = load_scan(arguments.config)
        report = (
            plan_scan(config)
            if arguments.plan
            else run_scan(
                config,
                spend=arguments.spend,
                credential=os.environ.get(CREDENTIAL),
            )
        )
    except (ConfigError, ScanRefusedError) as error:
        print(f"coldfix: {error}")
        return 1

    for line in report:
        print(line)
    return 0
