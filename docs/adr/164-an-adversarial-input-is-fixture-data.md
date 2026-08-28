# 164 — An adversarial input is fixture data

**Status:** accepted
**Date:** 2026-08-28

## Context

S-10.2 compares what a workload returns before and after a patch, and sweeps seven
input classes. `Probe` is what turns one input into one output, and nothing in
`src/` built one: the attack existed and could not reach a subject.

Building one requires deciding what an *input* is, and the answer is read off the
classes rather than chosen.

## Decision

**`coldfix_input` is fixture data, not request parameters.**

The seven classes are `EMPTY`, `NULL`, `DUPLICATES`, `TIES`, `UNICODE`,
`BOUNDARY` and `UNORDERED`. Every one is a property of the rows a workload reads;
none is a property of a query string. So the script seeds the subject with the
input and then drives the route. A probe that passed the payload as `?params`
would be sweeping the router, and would report seven agreements about a workload
whose data never changed.

`probe_for(workload, *, path, model, settings)` produces the script.

**The wrapper is untouched.** `equivalence.harness()` already embeds the payload
`ensure_ascii=True`, compiles inside its guarded block, and refuses a script that
binds nothing. What was missing is the half that knows the settings module, the
model and the route — the subject's business, and therefore the campaign's to
supply.

**Three decisions inside the script.**

*It deletes before it seeds.* That is what makes `EMPTY` a testable class rather
than a hypothetical one: an input of `[]` has to mean the workload reads nothing,
and a probe that only ever added rows would measure *empty collection* against
whatever the previous input left behind. Sabotage confirms — removing the delete
fails that test and one other.

*The status travels beside the body.* They are different observations. A patch
that turns a 200 into a 500 returns no useful body, and two absent bodies are two
runs agreeing about nothing — which the attack reads as the patch surviving.

*`settings` is supplied, never detected.* `grounder_for`'s reason: a probe run
against a configuration that happens to import would compare two revisions of the
wrong application, and both would agree.

**A probe missing its path, model or settings is refused at construction**, because
the failure it prevents is invisible. It fails the same way on both revisions and
produces the same absence of output, which S-10.2 reads as the patch surviving —
an answer arrived at by measuring nothing. That is the same shape as
`Probe.__post_init__`'s existing refusal of an empty script, and the producer
inherits that one rather than restating it.

## The delete, stated plainly

`model.objects.all().delete()` is the most destructive line in this repository,
and the only thing that makes it acceptable is where it runs: inside a candidate
session, which is a throwaway container over a throwaway worktree, against a
database S-2.5 refuses to open if its URL looks like production. The docstring
says so rather than leaving a reader to infer it, because the same three lines
pointed anywhere else would be indefensible.

## Consequences

**Five of the six are real**: `hands`, `ground`, `bind`, `executor`, `probe`. Only
`measure` remains, and nothing assembles a `Resources`.

**The end-to-end tests are `slow` and stand up a real Django project.** The
alternative — asserting the script's text — tests that two files were written by
the same person. Four of the five drive the script through the real `harness()` in
a subprocess against a migrated subject, so an input goes in and a payload comes
back through the marker the attack reads.

**One test asserts the design rather than the behaviour**, deliberately: that the
script contains `objects.create(**row)` and not `data=coldfix_input`. The
behavioural tests would all still pass against a query-parameter probe on a route
that ignores its parameters, which is most routes.
