"""A patch cannot edit the thing that decides whether the patch worked.

`03-agents.md` calls this the oldest cheat there is: a model that cannot make
the code faster can always make the test agree with the code. The story note
puts a number on the countermeasure — 87.7% relative reduction in exploit rate
on the Reward Hacking Benchmark, more than any detector achieves.

So the tests are written as attempts. Each one builds a diff that a model with
the wrong incentive would produce, hands it to the applier, and asserts both
that it was refused *and* that the file on disk is unchanged. A rejection that
still wrote the file would pass a weaker test.

The sharp one is `test_a_rename_out_of_a_protected_directory_is_refused`.
`git apply --numstat` reports a rename by its destination only, so a diff moving
`tests/test_slow.py` to `src/harmless.py` is reported as touching one
unprotected path. An implementation that asked git what a patch touches — the
obvious one — deletes the test suite and reports success.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from conftest import git

from coldfix.sandbox.patching import (
    DEFAULT_PATCH_POLICY,
    PatchDidNotApplyError,
    PatchPolicy,
    ProtectedPathError,
    UnparsablePatchError,
    UnsafePathError,
    apply_patch,
    touched_paths,
)

REAL_TEST = "def test_is_fast():\n    assert measure() < 0.1\n"


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    """A small repository shaped like a subject: source, tests, a fixture."""
    root = tmp_path / "subject"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "tests" / "fixtures").mkdir()

    (root / "src" / "app.py").write_text("def slow():\n    return 1\n")
    (root / "tests" / "test_speed.py").write_text(REAL_TEST)
    (root / "tests" / "conftest.py").write_text("import pytest\n")
    (root / "tests" / "fixtures" / "rows.json").write_text("[1, 2, 3]\n")
    (root / "noxfile.py").write_text("SESSIONS = ['tests']\n")

    git(root, "init", "--initial-branch=main")
    git(root, "add", "-A")
    git(root, "commit", "-m", "subject")
    return root


def diff_for(worktree: Path) -> str:
    """The diff of whatever is currently uncommitted, then undo it.

    Building attack diffs by making the change and asking git for it keeps them
    honest: every diff in this file is one git itself produced and would apply.
    """
    git(worktree, "add", "--intent-to-add", "--all")
    patch = git(worktree, "diff", "HEAD")
    git(worktree, "reset", "--hard", "HEAD")
    git(worktree, "clean", "-fdq")
    return patch


# ------------------------------------------------------------- the attacks


def test_editing_a_test_file_is_refused(worktree: Path) -> None:
    """AC 4, and the reason the whole module exists."""
    (worktree / "tests" / "test_speed.py").write_text("def test_is_fast():\n    assert True\n")
    patch = diff_for(worktree)

    with pytest.raises(ProtectedPathError, match="test_speed"):
        apply_patch(patch, worktree=worktree)

    assert (worktree / "tests" / "test_speed.py").read_text() == REAL_TEST


def test_deleting_a_test_file_is_refused(worktree: Path) -> None:
    (worktree / "tests" / "test_speed.py").unlink()
    patch = diff_for(worktree)

    with pytest.raises(ProtectedPathError):
        apply_patch(patch, worktree=worktree)

    assert (worktree / "tests" / "test_speed.py").exists()


def test_adding_a_new_test_file_is_refused(worktree: Path) -> None:
    """A patch that adds a passing test is still a patch editing the oracle."""
    (worktree / "tests" / "test_convenient.py").write_text("def test_ok():\n    assert True\n")
    patch = diff_for(worktree)

    with pytest.raises(ProtectedPathError):
        apply_patch(patch, worktree=worktree)

    assert not (worktree / "tests" / "test_convenient.py").exists()


def test_a_rename_out_of_a_protected_directory_is_refused(worktree: Path) -> None:
    """The attack git's own reporting does not show.

    `git apply --numstat` names a rename by its destination only, so this diff
    reads as touching `src/harmless.py` and nothing else. The test suite is
    deleted and no protected path ever appears. Both sides of the rename have to
    be parsed out of the diff for this to be caught.
    """
    git(worktree, "mv", "tests/test_speed.py", "src/harmless.py")
    patch = git(worktree, "diff", "--cached", "-M")
    git(worktree, "reset", "--hard", "HEAD")

    assert "rename from tests/test_speed.py" in patch

    with pytest.raises(ProtectedPathError, match="test_speed"):
        apply_patch(patch, worktree=worktree)

    assert (worktree / "tests" / "test_speed.py").read_text() == REAL_TEST
    assert not (worktree / "src" / "harmless.py").exists()


def test_a_rename_into_a_protected_directory_is_refused(worktree: Path) -> None:
    git(worktree, "mv", "src/app.py", "tests/app.py")
    patch = git(worktree, "diff", "--cached", "-M")
    git(worktree, "reset", "--hard", "HEAD")

    with pytest.raises(ProtectedPathError):
        apply_patch(patch, worktree=worktree)

    assert (worktree / "src" / "app.py").exists()


def test_editing_conftest_is_refused(worktree: Path) -> None:
    """One file that changes what every test can see, without appearing in any."""
    (worktree / "tests" / "conftest.py").write_text("collect_ignore_glob = ['*']\n")
    patch = diff_for(worktree)

    with pytest.raises(ProtectedPathError, match="conftest"):
        apply_patch(patch, worktree=worktree)


def test_editing_a_fixture_is_refused(worktree: Path) -> None:
    """Fixtures are the input a measurement is taken over.

    Shrinking the dataset makes everything faster and measures nothing.
    """
    (worktree / "tests" / "fixtures" / "rows.json").write_text("[]\n")
    patch = diff_for(worktree)

    with pytest.raises(ProtectedPathError, match=r"rows\.json"):
        apply_patch(patch, worktree=worktree)


def test_editing_the_runner_configuration_is_refused(worktree: Path) -> None:
    """Deselecting a test is as effective as deleting it, and much quieter."""
    (worktree / "noxfile.py").write_text("SESSIONS = []\n")
    patch = diff_for(worktree)

    with pytest.raises(ProtectedPathError, match="noxfile"):
        apply_patch(patch, worktree=worktree)


def test_a_patch_touching_one_protected_file_applies_none_of_itself(worktree: Path) -> None:
    """The rejection is of the whole patch, never of the parts git disliked.

    A partially applied patch would leave the source edited and the test
    untouched, which looks exactly like a fix that works.
    """
    (worktree / "src" / "app.py").write_text("def slow():\n    return 2\n")
    (worktree / "tests" / "test_speed.py").write_text("def test_is_fast():\n    assert True\n")
    patch = diff_for(worktree)

    with pytest.raises(ProtectedPathError):
        apply_patch(patch, worktree=worktree)

    assert (worktree / "src" / "app.py").read_text() == "def slow():\n    return 1\n"
    assert (worktree / "tests" / "test_speed.py").read_text() == REAL_TEST


# --------------------------------------------------------- paths that escape


@pytest.mark.parametrize(
    "path",
    [
        "../outside.py",
        "src/../../outside.py",
        "/etc/passwd",
        "C:/Windows/system32/drivers/etc/hosts",
    ],
)
def test_a_path_outside_the_worktree_is_refused(worktree: Path, path: str) -> None:
    patch = textwrap.dedent(f"""\
        diff --git a/{path} b/{path}
        --- a/{path}
        +++ b/{path}
        @@ -1 +1 @@
        -old
        +new
        """)

    with pytest.raises(UnsafePathError):
        apply_patch(patch, worktree=worktree)


def test_a_case_variant_of_a_protected_directory_is_refused(worktree: Path) -> None:
    """Windows and macOS resolve `Tests/` and `tests/` to the same file.

    A case-sensitive rule would be bypassable there by changing one letter, so
    matching is case-insensitive everywhere — over-rejecting on Linux, which is
    the direction to err in.

    The filename is `helpers.py` and not `test_speed.py` deliberately. A file
    named `test_*.py` is caught by a filename rule whatever case its directory
    is in, so using one here would let this test pass with case-insensitivity
    removed — which is exactly what an earlier version of it did.
    """
    patch = textwrap.dedent("""\
        diff --git a/Tests/helpers.py b/Tests/helpers.py
        --- a/Tests/helpers.py
        +++ b/Tests/helpers.py
        @@ -1 +1 @@
        -old
        +new
        """)

    assert DEFAULT_PATCH_POLICY.matching_rule("tests/helpers.py") == "**/tests/**"

    with pytest.raises(ProtectedPathError):
        apply_patch(patch, worktree=worktree)


def test_a_quoted_path_is_refused_rather_than_half_decoded(worktree: Path) -> None:
    """Fail closed. A path this filter may have misread is one it cannot rule on.

    Git C-quotes names containing control or non-ASCII bytes. Decoding that
    almost correctly is worse than refusing: the failure would be a protected
    path matching as a different string here than it names on disk.
    """
    patch = textwrap.dedent("""\
        diff --git "a/tests/caf\\303\\251.py" "b/tests/caf\\303\\251.py"
        --- "a/tests/caf\\303\\251.py"
        +++ "b/tests/caf\\303\\251.py"
        @@ -1 +1 @@
        -old
        +new
        """)

    with pytest.raises(UnparsablePatchError, match="quoted path"):
        apply_patch(patch, worktree=worktree)


# ------------------------------------------------------------ parsing itself


def test_content_that_looks_like_a_header_is_not_read_as_one() -> None:
    """The parser tracks hunk line counts rather than scanning line starts.

    A removed line whose text begins `-- a/x` renders as `--- a/x`, which is
    indistinguishable from a file header to anything that only looks at
    prefixes. Here the body mentions a protected path and must not be treated as
    touching it.
    """
    patch = textwrap.dedent("""\
        diff --git a/docs/notes.md b/docs/notes.md
        --- a/docs/notes.md
        +++ b/docs/notes.md
        @@ -1,3 +1,3 @@
         An example diff:
        --- a/tests/test_speed.py
        +++ b/tests/test_speed.py
        """)

    assert touched_paths(patch) == {"docs/notes.md"}


def test_both_sides_of_a_rename_are_reported() -> None:
    patch = textwrap.dedent("""\
        diff --git a/tests/test_a.py b/src/harmless.py
        similarity index 100%
        rename from tests/test_a.py
        rename to src/harmless.py
        """)

    assert touched_paths(patch) == {"tests/test_a.py", "src/harmless.py"}


def test_a_created_file_reports_only_the_side_that_exists() -> None:
    patch = textwrap.dedent("""\
        diff --git a/src/new.py b/src/new.py
        new file mode 100644
        --- /dev/null
        +++ b/src/new.py
        @@ -0,0 +1 @@
        +added
        """)

    assert touched_paths(patch) == {"src/new.py"}


# --------------------------------------------------------- what is allowed


def test_a_patch_that_only_touches_source_applies(worktree: Path) -> None:
    """The gate has to let real fixes through, or it is just a refusal."""
    (worktree / "src" / "app.py").write_text("def slow():\n    return 42\n")
    patch = diff_for(worktree)

    written = apply_patch(patch, worktree=worktree)

    assert written == {"src/app.py"}
    assert (worktree / "src" / "app.py").read_text() == "def slow():\n    return 42\n"


def test_a_patch_adding_a_source_file_applies(worktree: Path) -> None:
    (worktree / "src" / "cache.py").write_text("STORE = {}\n")
    patch = diff_for(worktree)

    assert apply_patch(patch, worktree=worktree) == {"src/cache.py"}
    assert (worktree / "src" / "cache.py").read_text() == "STORE = {}\n"


def test_a_patch_that_does_not_fit_is_reported_as_such_not_as_a_rejection(
    worktree: Path,
) -> None:
    """A patch nothing is wrong with, against a tree that has moved on.

    Distinct from a protected-path rejection: conflating them would tell the
    caller it tried to cheat when it merely raced.
    """
    (worktree / "src" / "app.py").write_text("def slow():\n    return 42\n")
    patch = diff_for(worktree)
    (worktree / "src" / "app.py").write_text("something else entirely\n")

    with pytest.raises(PatchDidNotApplyError):
        apply_patch(patch, worktree=worktree)


# --------------------------------------------------------------- the policy


def test_protected_paths_are_configurable(worktree: Path) -> None:
    """AC 3. A project can add its own, and the defaults still stand alone."""
    policy = PatchPolicy(protected=(*DEFAULT_PATCH_POLICY.protected, "**/src/app.py"))
    (worktree / "src" / "app.py").write_text("def slow():\n    return 42\n")
    patch = diff_for(worktree)

    with pytest.raises(ProtectedPathError, match=r"src/app\.py"):
        apply_patch(patch, worktree=worktree, policy=policy)

    assert apply_patch(patch, worktree=worktree) == {"src/app.py"}


def test_a_narrower_policy_can_be_chosen_and_the_default_is_not_it(worktree: Path) -> None:
    """Configurability cuts both ways, and the safe default is what ships.

    A caller may hand in an empty policy. What matters for AC 3 is that they
    must do so explicitly — nothing reaches an unprotected state by omission.
    """
    (worktree / "tests" / "test_speed.py").write_text("def test_is_fast():\n    assert True\n")
    patch = diff_for(worktree)

    with pytest.raises(ProtectedPathError):
        apply_patch(patch, worktree=worktree)

    assert apply_patch(patch, worktree=worktree, policy=PatchPolicy(protected=())) == {
        "tests/test_speed.py"
    }


@pytest.mark.parametrize(
    ("path", "protected"),
    [
        ("tests/test_a.py", True),
        ("src/tests/helpers.py", True),
        ("src/test_helpers.py", True),
        ("src/helpers_test.py", True),
        ("conftest.py", True),
        ("deep/nested/conftest.py", True),
        (".github/workflows/ci.yml", True),
        ("src/coldfix/bench/execute.py", True),
        ("src/app.py", False),
        ("src/contest.py", False),
        ("docs/testing.md", False),
        ("src/latest/thing.py", False),
    ],
)
def test_the_default_rules_match_what_they_claim_to(path: str, protected: bool) -> None:
    """`**` spans segments and `*` does not cross a `/`.

    The negative cases are the point. `latest/` contains "test" as a substring
    and is ordinary source; a rule implemented with a plain substring search or
    with `fnmatch` over the whole path would protect it and quietly refuse
    legitimate patches.
    """
    assert (DEFAULT_PATCH_POLICY.matching_rule(path) is not None) is protected
