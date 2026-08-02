# S-0.8 — Can a model select the right instrument?

**Status: built, not yet run.** The environment this was written in has no API
credentials. Run it with a key present and fill in `FINDINGS.md`.

## Why this spike exists

`00-BRIEF.md` §1 states the project's central claim:

> **Why an agent is required.** The methods are well-established and
> mechanizable. *Choosing which one applies to a given program*, sequencing
> them, and interpreting the results is documented in the fault-localization
> literature as requiring expert knowledge of the specific program. **That
> selection problem is the agent's job**, and the field named it as the
> bottleneck decades before LLMs existed.

That claim is tested in exactly one place — **S-8.7, marked *the thesis
behaviour***, whose own note says *"this is the demo that justifies the entire
architecture."* S-8.7 sits behind E1 through E7.

So E0 ran three spikes that proved the machinery is buildable, and none that
tested whether the central claim is true. This one does, cheaply, because
**testing the selection step needs no instruments — only recorded results, which
the E0 spikes already produced.**

## What it does

Six scenarios, each built from measurements a spike actually took. The model is
given the evidence and chooses the next experiment. Responses are constrained to
a schema, so scoring compares structured fields rather than reading prose —
which matters when the scorer and the subject are the same model family.

Three scores, kept separate because they fail independently:

| Score | Question |
|---|---|
| **instrument** | Did it pick a defensible next experiment? |
| **trap** | Did it stay out of the plausible-but-wrong conclusion? |
| **finding discipline** | Did it agree a finding was, or was not, warranted? |

A model can pick a sensible instrument *and* manufacture a finding from noise.
Under a single aggregate score that combination looks like a pass.

## The scenarios

| Scenario | The trap |
|---|---|
| `real_n_plus_one` | none — the control. 1193 queries for 100 tickets, growing with rows |
| `decoy_fixed_floor` | 37 queries, **constant** with dataset size. "Many queries" is not an N+1 |
| `over_fetch_invisible_to_query_count` | 1 query for both variants; only the guard counter separates them. "Query count is flat, therefore not the database" is wrong |
| `post_ablation_residual` | stopping at the first finding, when 504 customfield queries remain underneath |
| `flat_queries_time_grows` | staying on query counting when the instrument must change — S-8.7's own demo |
| `noise_no_finding` | **p = 0.008 with every guard counter identical.** The correct answer is *no finding* |

The last one is the one that matters. It is real data from S-0.4's null trials:
a 7.4 ms shift that reads as statistically significant and sits well inside the
12.76 ms envelope the identical-condition-against-itself trials produced. A model
that reports a finding here has violated the invariant that null results are
valid output — from numbers a careless reading calls significant.

## Run it

```bash
cd spikes/S-0.8-instrument-selection
ANTHROPIC_API_KEY=... uv run --with anthropic python run.py --repeats 10
```

`--repeats` exists because a single answer is an anecdote. The consistency
figure is the result; a model that gets the decoy right 6 times in 10 has not
passed.

`anthropic` is pulled in per-run with `--with` rather than added to the project's
dependencies — this is a spike, and E5 will make the real SDK decision when
there is production code to put it in (ADR-002).

## What a result means

- **Trap avoidance is high and finding discipline is high** — the selection step
  is sound, and S-8.7 is likely to work once the instruments exist. E1–E7 is
  de-risked.
- **Trap avoidance is high but finding discipline is not** — the model reasons
  well and cannot be trusted to stop. That is an argument for making E9's finding
  audit non-optional rather than a later refinement.
- **Trap avoidance is poor** — the thesis is in trouble, and it is far better to
  know that now than after E1 through E7 are built.

Whatever the outcome, it belongs in `FINDINGS.md` with the bounds stated: six
scenarios drawn from one framework, presented as text rather than discovered by
the model itself, and scored against answers a human worked out with the same
measurements in hand.
