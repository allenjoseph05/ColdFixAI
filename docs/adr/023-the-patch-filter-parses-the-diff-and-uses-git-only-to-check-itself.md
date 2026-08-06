# 023 — The patch filter parses the diff, and uses git only to check itself

**Status:** accepted
**Date:** 2026-08-06

## Context

S-2.4 requires `apply_patch(diff)` to reject any diff touching tests, fixtures,
`conftest`, the harness or instrumentation, server-side, with configurable but
safe defaults. Its note supplies the justification: environmental hardening of
this shape cut exploit rates by **87.7% relative** on the Reward Hacking
Benchmark, and *this single gate does more work than any detector*.
`03-agents.md` names what it defends against — *the oldest cheat there is*. A
model that cannot make the code faster can always make the test agree with the
code.

The rule is one sentence. Deciding **which paths a diff touches** is the whole
of the difficulty, and the obvious implementation is wrong.

## Decision

**The diff is parsed here. Git is consulted only to check that parse.**

The obvious implementation asks git — `git apply --numstat` — what a patch
touches. Measured before writing any code, `--numstat` **reports a rename by its
destination only**:

```
$ git apply --numstat <<< "rename tests/test_a.py -> src/harmless.py"
0   0   src/harmless.py
```

A diff that renames `tests/test_slow.py` to `src/harmless.py` therefore reads as
touching one unprotected path. The test file is deleted, no protected path is
ever seen, and the filter reports success. That is a complete bypass of the
project's highest-leverage control, available to anyone who knows git renames
files.

`git apply --summary` does know about the rename, but compacts the paths —
`rename tests/deep/{test_a.py => test_renamed.py} (100%)` — and recovering two
paths from that means guessing about filenames containing braces or ` => `.

So this module parses the diff, taking **both sides** of every rename and copy,
and then asks git for its destinations purely as a **cross-check**: if git
reports a path the parser did not find, the patch is rejected as unparsable. The
filter must be a superset of git's view or it is not a filter.

**The parser tracks hunk state.** Inside a hunk every line begins with a space,
`+`, `-` or `\`, so a removed line whose content starts `-- a/x` renders as
`--- a/x` and is indistinguishable from a file header to anything scanning line
prefixes. Consuming the line counts declared by each `@@` header is what tells
content from structure, and it removes both the false negative (a header missed)
and the false positive (a documentation file that quotes a diff being read as
touching the paths it mentions).

**Matching is case-insensitive.** Windows and macOS resolve `Tests/helpers.py`
and `tests/helpers.py` to the same file, so a case-sensitive rule is bypassable
there by changing one letter. On Linux the two are genuinely different files and
this over-rejects, which is the correct direction.

**`**` spans path segments and `*` does not cross a `/`.** Written out rather
than delegated, because `PurePath.full_match` arrived in 3.13 and this project
targets 3.12, and because `fnmatch` over a whole path lets `*` cross separators
— which would make `**/test_*.py` and `*test_*.py` equivalent and silently
protect `src/latest/thing.py`.

**Failure is always rejection.** A quoted path is refused rather than decoded:
git C-quotes names containing control or non-ASCII bytes, and a filter that
decodes them almost correctly is worse than one that refuses, because the
failure mode is a protected path matching as a different string here than it
names on disk. A malformed hunk abandons its line counts, which over-collects.
Every ambiguity resolves toward refusing.

**The audit runs before anything is written**, and rejection is of the whole
patch. A partially applied patch would leave the source edited and the test
untouched, which is indistinguishable from a fix that works.

**The filter lives in the applier**, which is the only route by which a diff
becomes a file, and is reached through `CandidateSession.apply_patch`. A
diagnostic session has no `apply_patch` for the same reason it has no `diff`
(ADR 022) — the capability is absent, not guarded.

## Consequences

**Makes easy.** Answering "could this system have edited its own tests" by
reading one function. Adding a project's own protected paths without touching
code: `PatchPolicy` takes a tuple, and an adapter is the eventual source of the
per-project entries.

**Makes hard.** Patching a file whose name contains non-ASCII characters — it
is refused as unparsable. That is a real limitation on non-English repositories
and it is a deliberate trade: correct refusal over probably-correct decoding.
Also hard: a legitimate patch that renames a file out of a directory named
`test`, which is refused even when the file is not a test.

**Rules out.** Trusting git's own summary of what a patch touches, which is the
implementation this replaces and which is silently bypassable.

**Left open.** Symlinks. A patch that edits a file which is a symlink to a
protected path is not detected here — the path in the diff is the link's name.
Git refuses to follow symlinks when applying patches (it has since 2.32, after
CVE-2021-21300), so the current protection is git's rather than this module's.
When S-7.x stands up repositories that were not created by this system, that
assumption is worth testing rather than inheriting.

## Provenance

`docs/10-BACKLOG.md` S-2.4 and its note; `03-agents.md` §5.5 and §7; ADR 006,
which already owed this gate the same shape.

The `--numstat` rename behaviour is not documented in a way that makes the
consequence obvious. It was found by constructing the attack against a scratch
repository before writing the module, and
`test_a_rename_out_of_a_protected_directory_is_refused` reproduces it.

**Sabotage-verified, five properties, and one of them exposed a bad test.**
Dropping rename-source parsing fails 2 tests. Applying before auditing fails 2.
Skipping the escape check fails 4. Letting `*` cross a `/` fails the
`src/latest/thing.py` case. Making matching case-sensitive **failed nothing**,
because the test used `Tests/test_speed.py`, and `**/test_*.py` matches that
filename whatever case its directory is in — the directory rule was never
exercised. The test now uses `Tests/helpers.py`, where only the directory rule
can catch it, and it fails under the sabotage. This is the second time in Epic 2
that a test passed for a reason other than the one it claimed (cf. ADR 021), and
both were found by sabotage rather than by review.
