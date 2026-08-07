# 014 — `diff()` is strict by default, and every loosening is opt-in

**Status:** accepted
**Date:** 2026-08-03

## Context

`diff()` is not like the other lab-bench operations. `execute`, `time` and
`count` produce facts a human or an agent then reasons about. This one produces
a **verdict a patch is gated on** — output equivalence is how the system decides
that a fix changed the cost of a program without changing its behaviour.

That inverts which failure matters. A false "differs" wastes a cycle and is
visible. A false "identical" does not fail anything: it approves a patch, and
the approval looks exactly like a correct one. Every default here is chosen on
that asymmetry.

S-1.4's note names one instance — order-insensitivity must be opt-in per
comparison, because defaulting to it hides real regressions. Implementing it
turned up several more of the same shape, all of them cases where the obvious
Python answer is the unsafe one.

## Decision

**Strict by default. Each loosening is a named argument on a single call.**

`ignore_order` is decided per comparison, from whether the query behind the
payload had an `ORDER BY`. It is not a session setting, not a project setting,
and never inferred: for a sorted endpoint the ordering *is* the behaviour, and
for a paginated one a changed order silently changes which rows a client sees on
page two. It compares **multisets, not sets** — `[1, 1, 2]` and `[1, 2, 2]`
differ, which is precisely the shape of a patch that duplicated one row and
dropped another — and it applies at every depth.

`float_tolerance` defaults to exact. A tolerance nobody chose is a tolerance
that hides a rounding regression. When set, it bounds both relative and absolute
difference, because near zero a relative bound demands infinite precision.

**JSON types, not Python types.** JSON has one number type, so `1` and `1.0`
agree. It has a separate boolean type, so `True` and `1` do not — even though
Python's `==` says they are equal and `isinstance(True, int)` is true. Ordering
those two checks the other way round makes `{"ok": true}` and `{"ok": 1}`
identical. Likewise `null` and an absent key are different payloads, reported
against a distinct `ABSENT` sentinel rather than `None`; a `str` is not an array
of characters, though it satisfies `Sequence`; and `bytes` is refused rather
than read as an array of integers, which is the failure mode of passing an
unparsed response body.

Two NaNs agree. IEEE 754 says otherwise, but under `==` a payload containing one
would differ from itself, so no patch touching it could ever be verified — not
even against a rerun of unmodified code.

**Paths are structural, and rendered separately.** A difference carries
`("results", 3, "price")` and renders it as `$.results[3].price`. Pre-rendering
would make a key containing a dot indistinguishable from a path separator.
Traversal visits object keys sorted, so "the first difference" does not depend
on which payload happened to be serialized first.

**Values are accepted as `Mapping` and `Sequence`, not `dict` and `list`**,
because those are covariant in their contents and `dict` is not: a literal
`{"results": [{"id": 1}]}` is a `dict[str, list[dict[str, int]]]`, which is not
assignable to `dict[str, JsonValue]`. Requiring exactness would put a cast at
every call site that builds a payload rather than parsing one.

## Consequences

**Makes easy.** The dangerous direction requires an explicit argument at the
call site, where the person writing it knows the endpoint. Reviewing a
comparison means reading one line.

**Makes hard.** Callers with literal heterogeneous payloads need a `JsonValue`
annotation, since mypy infers `dict[str, object]`. Payloads from `json.loads`
need nothing. Unordered comparison **with** a tolerance is quadratic and greedy:
approximate equality is not transitive, so no canonical key exists and no linear
matching is possible. Greedy pairing can report a difference that a maximum
matching would not — the conservative direction, and acceptable for a check that
gates patches, but if a real workload ever needs it this wants a proper bipartite
matching rather than a threshold nudge.

**Rules out.** Comparing anything that is not JSON. A `datetime` or a `Decimal`
is refused rather than compared with `==`, because the fallback for an
unrecognized type is identity comparison — under which two equal values from
separate runs are "different" and two references to one object are "identical".
Both are wrong and neither announces itself. Callers serialize first.

Note also what this module deliberately does not do: it takes no position on
whether a difference *matters*. A changed `updated_at` timestamp is reported
like any other. Deciding which differences are acceptable is a judgement, and
judgement belongs to an agent working from evidence, not to an instrument.

## Provenance

`docs/10-BACKLOG.md`, S-1.4 note (order-insensitivity opt-in per comparison,
decided by `ORDER BY`). The remaining cases were found while implementing it,
and each has a test that attempts the false "identical" and asserts it fails.
Two were verified by sabotage: defaulting `ignore_order` to true, and replacing
the multiset with a set, each fail exactly the tests written for them.
