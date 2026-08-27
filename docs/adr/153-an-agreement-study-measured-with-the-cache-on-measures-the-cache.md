# 153 — An agreement study measured with the cache on measures the cache

**Status:** accepted
**Date:** 2026-08-27

## Context

S-15.1 asks for the number `00-BRIEF.md` §6 calls the headline one: run diagnosis
N times independently on one repository with the cache disabled, report agreement
on the primary finding as a percentage and the distribution of alternatives, at
N=10 minimum.

It is the honest form of *reliable* precisely because it is easy to report as
something better than it is. Three ways, and each is arithmetic somebody would
write without noticing.

## Decisions

### 1. A cached run is refused, not down-weighted

S-5.1's replay cache keys on `(repo_sha, workload_id, experiment_spec,
fixture_hash)` and returns the recorded answer. Ten cached runs are **one run
reported ten times**, and the study would come back at 100%.

That is not a weaker measurement of agreement. It is a measurement of the cache,
and the two are not on the same scale — so the artifact cannot be constructed
over one. A caveat attached to a 100% figure is read by nobody who wanted the
100%.

A single cached run among ten is enough to refuse, because the study is over the
set.

### 2. A null result is an outcome, not an exclusion

Nine runs finding nothing and one finding a cause do not agree about that cause.
They agree, nine to one, that there is nothing to find — and the disagreement is
the interesting half.

The obvious implementation counts findings, and counting findings drops the
nulls: the study then reports **100% agreement over a single run**. So `None` is
a key in the distribution like any other, and a repository where every run agrees
there is nothing renders as *primary: nothing found*, at 100%, which is a good
result rather than an empty one.

This is `00-BRIEF.md` §9's rule — null results are valid output — reaching the
evaluation layer, where the temptation to drop them is strongest because they are
the ones that make the number look worse.

### 3. A tie has no primary finding, and `None` cannot carry that

Five runs saying one thing and five saying another have no modal answer. Naming
either would invent a winner from an arithmetic accident.

`None` cannot be the signal, because `None` already means *nothing found was the
modal answer*. So the tie is a third state: `modal_outcomes` is the primitive —
every outcome tied for most frequent — and `primary` **raises** on a tie rather
than answering. A property that raises is the same construction `Selection.get`
uses, and it makes *read the tie first* structural instead of advisory.

The rendered report leads with the tie and says **that is the result**: a tool
whose answer depends on which run you look at has not been shown to have one.

### 4. The point estimate carries an interval

Eight out of ten is 80% with a 95% Wilson interval of roughly 49% to 94%.
Publishing the point estimate alone lets a reader take it for a measurement of a
system rather than of ten runs of one. `wilson` is imported from
`eval/ablation.py`, which `eval/learning.py` already does — one implementation,
three callers.

### 5. `flipped` is separate from the rate, and S-15.4 is why

Nine-to-one is 90% **and** has flipped. A failure catalogue reading only the rate
would record the repository as reliable and lose the one run that disagreed —
and *diagnoses that flipped between runs* is one of S-15.4's own acceptance
criteria. So the fact that more than one outcome occurred is its own property,
not something inferred from the percentage.

That is also the seam S-15.4 consumes, which is why this story came first: its
AC 3 has no source without it.

### 6. The finding key is the caller's, and must be measured

Two runs describing one cause in different words are not a disagreement, and two
different causes described alike are not an agreement. So the key has to come
from a measurement — for this system, the causal site `primitives.localization`
walks to — and deriving it is the caller's, for the reason `factory_seeder`'s
module path is: this module cannot know how the caller identifies a finding, and
guessing would put a judgement where a fact belongs.

## Consequences

**It runs nothing**, like `eval/ablation.py` and `eval/learning.py`. `CLAUDE.md`
forbids a study that takes its own measurements, and a study that drove the
pipeline itself could not be re-run against recorded results without spending the
corpus again. A test asserts the signature has no parameter through which a
pipeline could be handed to it.

**The run costs money and the harness does not.** Building this needed no API
key; producing the ten runs it consumes does, at ten investigations. That is the
same split the no-spending rule has taken everywhere: the harness is buildable
now, the study is a number for later.

**S-15.4 is unblocked.** Its AC 3 needed a source for *diagnoses that flipped*
and now has `Agreement.flipped` and `Agreement.distribution`.

**Sabotage: 5 properties, 5 caught** — dropping the nulls from the distribution,
accepting cached runs, naming a leader on a tie, accepting fewer than ten runs,
and inferring a flip from the rate instead of from the outcomes.
