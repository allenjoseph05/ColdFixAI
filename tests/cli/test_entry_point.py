"""`coldfix` — the command that starts a run. **S-17.18.**

The library was finished before anything could start it. The tests that matter
are the ones about the two ways that goes wrong: a configuration file that is
subtly incomplete, and a command that spends money because it was easy to type.
"""

from __future__ import annotations

import tomllib
from decimal import Decimal
from pathlib import Path

import pytest

from coldfix.cli.config import ConfigError, load
from coldfix.cli.main import CREDENTIAL, build_parser, main, plan, run
from coldfix.cli.wiring import ADAPTERS, WiringError, adapter_for

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

REPOSITORY = Path(__file__).resolve().parents[2]


def written(tmp_path: Path, text: str = COMPLETE) -> Path:
    path = tmp_path / "coldfix.toml"
    path.write_text(text, encoding="utf-8")
    return path


def without(section: str, key: str) -> str:
    """`COMPLETE` with one key removed, so the refusal has one cause."""
    lines, inside, kept = COMPLETE.splitlines(), False, []
    for line in lines:
        if line.startswith("["):
            inside = line.strip() == f"[{section}]"
        if inside and line.split("=")[0].strip() == key:
            continue
        kept.append(line)
    return "\n".join(kept)


# ==================================== a file supplies everything that is not an object


def test_a_complete_file_produces_every_value_a_run_needs(tmp_path: Path) -> None:
    """AC 1. Twenty-five arguments, fourteen of them from here and nothing else."""
    config = load(written(tmp_path))

    assert config.project == "shop"
    assert config.framework == "Django"
    assert config.python == ("python",)
    assert config.suite_command == ("pytest", "-q")
    assert config.ceiling_eur == Decimal("25.00")
    assert config.rate_as_of.isoformat() == "2026-08-30"
    assert config.prefix_tokens == 2048
    assert config.claim.guards == (("response_bytes", 2000.0, 3000.0),)


def test_a_ceiling_is_read_from_a_string_rather_than_a_float(tmp_path: Path) -> None:
    """**The one place a float would matter.** A euro ceiling parsed from a TOML
    float is very slightly not the number that was written, and the comparison it
    is used in is the one that stops a run."""
    config = load(written(tmp_path))

    assert config.ceiling_eur == Decimal("25.00")
    with pytest.raises(ConfigError, match="quoted decimal"):
        load(written(tmp_path, COMPLETE.replace('ceiling_eur = "25.00"', "ceiling_eur = 25.00")))


# ============================================== a bad file says which field, and where


def test_a_missing_file_says_so_rather_than_raising_oserror(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no configuration at"):
        load(tmp_path / "absent.toml")


def test_invalid_toml_is_reported_as_invalid_toml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not valid TOML"):
        load(written(tmp_path, "[project\nname = 'shop'"))


@pytest.mark.parametrize(
    ("section", "key"),
    [
        ("project", "framework"),
        ("subject", "database_url"),
        ("subject", "metric"),
        ("tokens", "prefix"),
        ("store", "url"),
    ],
)
def test_a_missing_field_names_the_section_and_the_key(
    tmp_path: Path, section: str, key: str
) -> None:
    """AC 5. **A `KeyError` tells somebody a dictionary lacked a key.** This file
    is the first thing a new user writes, and the message is most of the
    experience of writing it."""
    with pytest.raises(ConfigError, match=rf"\[{section}\]\.{key} is required"):
        load(written(tmp_path, without(section, key)))


def test_a_missing_section_says_which_section(tmp_path: Path) -> None:
    text = COMPLETE.split("[tokens]", maxsplit=1)[0] + COMPLETE.split("[claim]", 1)[1]
    with pytest.raises(ConfigError, match=r"has no \[tokens\] section"):
        load(written(tmp_path, text))


@pytest.mark.parametrize(
    ("bad", "wanted"),
    [
        ('python        = "python"', "a non-empty list of strings"),
        ("prefix = -1", "a non-negative whole number"),
        ('rate_as_of  = "the thirtieth"', "a date such as"),
    ],
)
def test_a_value_of_the_wrong_type_says_what_was_wanted(
    tmp_path: Path, bad: str, wanted: str
) -> None:
    original = {
        'python        = "python"': 'python        = ["python"]',
        "prefix = -1": "prefix = 2048",
        'rate_as_of  = "the thirtieth"': "rate_as_of  = 2026-08-30",
    }[bad]
    with pytest.raises(ConfigError, match=wanted):
        load(written(tmp_path, COMPLETE.replace(original, bad)))


# ============================================ the adapter supplies the rest


def test_the_adapter_is_resolved_from_the_framework_the_file_names(tmp_path: Path) -> None:
    """AC 2."""
    config = load(written(tmp_path))

    assert type(adapter_for(config.framework)).__name__ == "DjangoAdapter"


def test_an_unknown_framework_lists_what_can_be_driven() -> None:
    """The likely cause is a typo, and the fix is visible once the alternatives
    are on screen."""
    with pytest.raises(WiringError, match="This system can drive: Django, Flask"):
        adapter_for("Djnago")


def test_every_adapter_class_is_reachable_from_the_wiring() -> None:
    """**ADR 050's discipline, for the third registry in this codebase.**

    Written out rather than discovered by scanning, because a scan finding
    nothing is indistinguishable from a framework with no adapter. This is what
    keeps the written list honest.
    """
    declared = {
        path.stem
        for path in (REPOSITORY / "src" / "coldfix" / "adapters").glob("*.py")
        if "class " in path.read_text(encoding="utf-8")
        and "def framework(" in path.read_text(encoding="utf-8")
    }
    wired = {name.lower() for name in ADAPTERS}

    assert declared <= wired | {"interface", "conformance"}, (
        f"an adapter declares a framework and nothing wires it: {declared - wired}"
    )


# ================================================ plan spends nothing


def test_plan_reports_the_run_without_a_model_client(tmp_path: Path) -> None:
    """AC 3. **No `ModelClient` is constructed and none is asked for.**

    `plan` deliberately does less than `campaign_for`: opening a workbench needs
    Docker and opening the store needs Postgres, and a command whose whole
    purpose is *have I configured this correctly* should not require both to
    answer that.
    """
    report = "\n".join(plan(load(written(tmp_path))))

    assert "shop @ HEAD" in report
    assert "DjangoAdapter" in report
    assert "db.query" in report
    assert "25.00 EUR" in report


def test_plan_says_whether_the_framework_can_actually_be_grounded(tmp_path: Path) -> None:
    """An adapter existing and grounding support being registered are different
    facts — S-14.6 — and a plan that showed only the first would report a run
    that the fingerprint will refuse."""
    report = "\n".join(plan(load(written(tmp_path))))

    assert "groundable   yes" in report
    assert "registered: Django" in report


def test_planning_a_framework_with_an_adapter_but_no_grounding_says_so(tmp_path: Path) -> None:
    """Flask has an adapter and registers no grounding support, which is exactly
    the state ADR 148 §2 recorded. The plan has to show the difference."""
    report = "\n".join(plan(load(written(tmp_path, COMPLETE.replace("Django", "Flask")))))

    assert "groundable   no" in report
    assert "would be refused at the fingerprint" in report


# ================================================ run refuses unless told to spend


def test_run_without_the_flag_refuses_and_spends_nothing(tmp_path: Path) -> None:
    """AC 4. **The flag is not a confirmation prompt.** Prompts are answered by
    habit; this has to be typed on purpose."""
    with pytest.raises(Exception, match="was not given --spend"):
        run(load(written(tmp_path)), spend=False, credential="sk-test")


def test_run_with_the_flag_and_no_credential_refuses_before_opening_anything(
    tmp_path: Path,
) -> None:
    """Refused here rather than after standing up a container and a database."""
    with pytest.raises(Exception, match=f"{CREDENTIAL} is not set"):
        run(load(written(tmp_path)), spend=True, credential=None)


def test_the_default_invocation_cannot_spend(tmp_path: Path) -> None:
    """The property, asserted through the parser rather than through the
    functions: `run` requires a subcommand *and* a flag, so no single-word
    invocation of this tool costs money."""
    parser = build_parser()

    assert parser.parse_args(["--config", str(written(tmp_path)), "plan"]).command == "plan"
    assert not parser.parse_args(["run"]).spend


def test_running_without_a_subcommand_prints_help_and_fails(tmp_path: Path) -> None:
    del tmp_path
    assert main([]) == 2


def test_a_bad_configuration_exits_nonzero_rather_than_tracebacking(tmp_path: Path) -> None:
    """A traceback is a bug report; this is a typo in a file the user wrote."""
    bad = written(tmp_path, without("subject", "database_url"))

    assert main(["--config", str(bad), "plan"]) == 1


def test_a_good_configuration_plans_successfully(tmp_path: Path) -> None:
    assert main(["--config", str(written(tmp_path)), "plan"]) == 0


def test_the_shipped_example_is_a_configuration_that_actually_loads() -> None:
    """**The file the refusal points people at.**

    `load` tells somebody with no configuration to copy `coldfix.example.toml`.
    An example that had drifted out of step with the loader would send them
    somewhere worse than nowhere, and it is the file most likely to drift because
    nothing else reads it.
    """
    config = load(REPOSITORY / "coldfix.example.toml")

    assert config.framework in ADAPTERS
    assert plan(config)


# ================================================ the console script exists


def test_pyproject_declares_the_command_and_it_is_importable() -> None:
    """AC 6. A declared entry point naming a function that does not exist fails at
    install time for the user and never for us."""
    manifest = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))

    target = manifest["project"]["scripts"]["coldfix"]

    assert target == "coldfix.cli.main:main"
    assert callable(main)
