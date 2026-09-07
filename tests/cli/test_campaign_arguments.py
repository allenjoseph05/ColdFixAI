"""`Config` translated into what `campaign_for` takes. **S-17.1.**

`coldfix run` refused to proceed past its two guards until now, and the reason
`cli/main.py` gave was that wiring an unrun path and calling it done is how a
system arrives at its first real invocation with the confidence of code nobody
has executed.

That argument is against *pretending*, not against writing it. What it asks for
is that the untested surface be made as small as it can be, and that the part
which can be checked is checked properly. `campaign_arguments` is that part: it
opens no container, no database and no client, so every value a paid run would be
assembled from can be asserted here for nothing.

The assertion that matters most is the first. **The produced mapping covers
exactly the parameters `campaign_for` requires** — checked against the live
signature, because a missing key is a `TypeError` raised after the workbench, the
database and ten reset-verification cycles are already standing, which is the
most expensive possible place to find a typo.

The configuration below is a copy of `test_entry_point.py`'s rather than an
import of it: the test directories carry no `__init__.py`, and a cross-module
import that resolves under one pytest invocation and not another is a worse
dependency than eleven duplicated lines.
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from coldfix.cli.config import Config, load
from coldfix.cli.main import campaign_arguments, run_id_for
from coldfix.cli.wiring import adapter_for, supplied_by
from coldfix.orchestrator.assembly import campaign_for
from coldfix.repair.falsification import Guard

COMPLETE = """
[project]
name      = "shop"
root      = "."
revision  = "HEAD"
framework = "Django"
trust_key = "n-plus-one:uniform"

[subject]
python        = ["python"]
database_url  = "postgresql://localhost/shop_test"
settings      = "config.settings"
source        = "shop@HEAD"
suite_command = ["pytest", "-q"]
entity        = "author"
path          = "/books/"
model         = "shop.Book"
metric        = "db.query"

[workload]
id          = "books"
description = "the books list"

[budget]
ceiling_eur = "25.00"
rate_eur    = "0.92"
rate_as_of  = 2026-08-30

[tokens]
prefix = 2048
prompt = 12000

[claim]
metric   = "db.query"
baseline = 1193.0
at_most  = 12.0
guards   = [{ metric = "response_bytes", baseline = 2000.0, at_most = 3000.0 }]

[sandbox]
image         = "python:3.12"
worktree_root = ".coldfix/worktrees"

[store]
url = "postgresql://localhost/coldfix_knowledge"
"""


def written(directory: Path, text: str = COMPLETE) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "coldfix.toml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return load(written(tmp_path))


def arguments(config: Config) -> dict[str, object]:
    """The translation, with the three live objects stubbed.

    Stubbed rather than built because the translation never looks at them — it
    puts each one in a slot. Anything that inspected one would fail here, which is
    what makes the stub the right double.
    """
    adapter = adapter_for(config.framework)
    supplied = supplied_by(adapter, root=config.root, python=config.python, path=config.path)
    return campaign_arguments(
        config,
        supplied,
        client=object(),  # type: ignore[arg-type]
        workbench=object(),  # type: ignore[arg-type]
        store=object(),  # type: ignore[arg-type]
    )


# ========================================== the partition: every parameter, no extras


def test_it_supplies_exactly_the_parameters_campaign_for_requires(config: Config) -> None:
    """**The one worth having.**

    Read off the live signature rather than a hand-written list, so a parameter
    added to `campaign_for` fails here rather than at the first paid run. Both
    directions matter: a missing key raises `TypeError` after everything is open,
    and a surplus key raises the same `TypeError` one line later.
    """
    signature = inspect.signature(campaign_for)
    required = {
        name
        for name, parameter in signature.parameters.items()
        if parameter.default is inspect.Parameter.empty
    }
    optional = set(signature.parameters) - required

    produced = set(arguments(config))

    assert required <= produced, f"campaign_for would raise TypeError for {required - produced}"
    assert produced <= required | optional, (
        f"campaign_for takes no {produced - required - optional}"
    )


def test_nothing_arrives_as_none_in_a_slot_that_forbids_it(config: Config) -> None:
    """The looser half, kept because the strict one cannot be written.

    Several parameters are protocols or generics `isinstance` cannot check, and
    three are deliberately stubbed. What *can* be checked is that no value arrived
    as `None` in a slot whose annotation has no `None` in it — which is the shape
    a config field silently failing to load would take.
    """
    signature = inspect.signature(campaign_for)
    nullable = {
        name
        for name, parameter in signature.parameters.items()
        if "None" in str(parameter.annotation)
    }

    produced = arguments(config)
    absent = {name for name, value in produced.items() if value is None and name not in nullable}

    assert absent == set()


# =================================================== the pair that is easy to swap


def test_the_target_is_the_model_and_the_entity_is_the_entity(config: Config) -> None:
    """The confusable pair, and the reason this file exists at all.

    `Plan.entity` breaks a tie between equally-ranked factories; `Plan.target` is
    what synthesis seeds. The shipped example has `entity = "author"` against
    `model = "shop.Book"`, so they are different strings and swapping them is
    silent: `prefer` would tie-break alphabetically, seed the wrong table, and the
    workload would measure an empty list — the failure `Plan`'s own docstring
    names.

    `tests/orchestrator/test_epic17_composed.py` passes one value for both, so it
    could never have caught this.
    """
    plan = arguments(config)["plan"]

    assert config.entity != config.model, "the fixture has to keep the two distinguishable"
    assert plan.entity == config.entity == "author"  # type: ignore[attr-defined]
    assert plan.target == config.model == "shop.Book"  # type: ignore[attr-defined]


# =============================================================== the claim's guards


def test_guards_arrive_as_guards_not_as_the_triples_the_file_holds(config: Config) -> None:
    """TOML has no types, so the config carries `(metric, baseline, at_most)`
    triples and `CostClaim` wants `Guard`. This translation is the only place that
    conversion happens; a triple passed straight through would fail inside the
    patch audit rather than here.
    """
    claim = arguments(config)["claim"]

    assert claim.guards == (  # type: ignore[attr-defined]
        Guard(metric="response_bytes", baseline=2000.0, at_most=3000.0),
    )
    assert claim.metric == "db.query"  # type: ignore[attr-defined]
    assert claim.baseline == 1193.0  # type: ignore[attr-defined]


def test_the_ceiling_stays_decimal_rather_than_becoming_a_float(config: Config) -> None:
    """`ceiling_eur` is money, and the boundary is the only place a ceiling is ever
    consulted — which is exactly where a float compares wrong."""
    produced = arguments(config)

    assert produced["ceiling_eur"] == Decimal("25.00")
    assert isinstance(produced["ceiling_eur"], Decimal)
    assert produced["rate"].as_of == date(2026, 8, 30)  # type: ignore[attr-defined]


# ===================================================================== the run id


def test_the_run_id_is_stable_across_calls(config: Config) -> None:
    """A resumable thread or an unresumable one, and the difference is invisible.

    `resume` continues the thread `start` opened. An id carrying a timestamp would
    make every interrupted campaign look like a fresh one — and `invoke(None, ...)`
    against an unknown thread starts a new run rather than failing, which is the
    accident `ResumeError` exists to prevent one layer down.
    """
    assert run_id_for(config) == run_id_for(config)
    assert run_id_for(config) == "shop@HEAD"


def test_two_revisions_of_one_project_are_different_runs(tmp_path: Path) -> None:
    """The other half. One id for every revision would resume a thread whose
    checkpoints describe different code."""
    head = load(written(tmp_path / "head"))
    tagged = load(
        written(tmp_path / "tagged", COMPLETE.replace('revision  = "HEAD"', 'revision  = "v2"'))
    )

    assert head.revision == "HEAD"
    assert tagged.revision == "v2"
    assert run_id_for(head) != run_id_for(tagged)
