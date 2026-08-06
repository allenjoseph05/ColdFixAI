# 032 — A fixture shape is generated here so the volume cannot move with it

**Status:** accepted
**Date:** 2026-08-06

## Context

S-3.3 asks for fixtures in three distributions — uniform, power law, long tail —
the ability to hold volume constant while varying distribution, the distribution
recorded in every measurement, and a test proving a skew-dependent defect is
invisible under uniform data and visible under a power law.

The reason the story exists is stated in one line of its note: *an N+1 that costs
milliseconds at three related rows and minutes at three thousand is invisible if
every generated parent has exactly three children.* That is not a corner case,
because it is not about a rare defect — it is about the fixture generator almost
everyone writes. `build_store(authors, books_per_author)`, in this project's own
test fixtures, is exactly it.

And the blindness is provable rather than anecdotal. A per-parent cost of
`k(k-1)/2` totals `Σ k²` up to constants, and **for a fixed number of children
over a fixed number of parents, `Σ k²` is minimized exactly when every parent
holds the same count.** That is Cauchy-Schwarz. The uniform fixture is not merely
a weak choice for this class of defect; it is the provably weakest one, and no
increase in volume recovers what it hides.

## Decision

**The allocation is generated here, not by the caller, and every distribution
returns exactly `groups` counts summing to exactly `total`.** This is the whole
reason the story is more than an enum. A comparison in which the shape changed
*and* the row count changed attributes nothing to either, and the natural
implementation — round each share independently — silently produces 199 rows
under one shape and 201 under another. Largest-remainder apportionment makes the
totals exact, and the tests run it where rounding is worst: totals that do not
divide, and a spread of exactly one child per parent.

**Every parent gets at least one child.** A shape that quietly empties half the
parents has varied the parent count as well as the shape, which is the same
failure one level down.

**No random number generator anywhere.** The counts are computed, so the same
arguments give the same fixture on every machine and every Python version. A
measurement taken today has to be comparable with one taken next month, and
S-5.1 will key a replay cache on the fixture.

**The three shapes are defined so that they are three shapes**, and a test
enforces it on two statistics — mass concentration and spectrum. This is the
decision most at risk of being made carelessly, because three names over one
distribution passes every test that only exercises the machinery:

| Shape | Definition | Largest tenth holds | Spectrum |
|---|---|---|---|
| Uniform | every parent equal | a tenth | one size |
| Power law | Zipf, `count ∝ 1/rank` | most | smooth, many middling |
| Long tail | a tenth of parents take everything above the floor | nearly all | bimodal |

The first attempt defined the long tail as a head band holding 40% of the mass,
which at twenty parents gave a head mass of 0.37 against the power law's 0.39.
Those are one distribution with two names. **The long tail is now the shape data
engineers mean by the phrase** — *most customers have one order; one customer has
fifty thousand* — which is bimodal rather than smooth and is the deliberate worst
case for any per-parent cost. That is the shape the story's note describes, and
no generator produces it by accident.

**The baseline is subtracted here too, and it costs something different than it
does on the volume axis.** A volume sweep loses its *exponent* to an unsubtracted
floor; a shape comparison loses its *ratio*. Measured: a fixed 2,000 comparisons
charged by something unrelated to the data drags a 9.1× skew sensitivity down to
3.5×, always in the direction that makes a real finding look survivable.

**`sensitivity` returns infinity when the reference shape charged nothing.** That
is a real answer and the strongest form the finding takes — "invisible under
uniform data" is sometimes literal, for any per-parent cost with a threshold: a
chunked fetch, a pagination boundary. Zero against zero returns 1.0 instead,
because a metric nothing spends under any shape is not a finding and reporting it
as infinite sensitivity would manufacture one.

**`scale_volume` gained a required `distribution` argument in this story.** AC 3
says the distribution is recorded in *every* measurement, and a volume sweep uses
fixtures too — a growth curve measured under uniform data is precisely the result
the note warns is blind. It is declared by the caller rather than observed, the
way `time()` takes `fresh_process_per_sample`: the function is handed a seeding
callable and cannot see what it generated. Changing the previous story's
signature is deliberate; this is the story that makes the omission visible.

**Both axes live in one module.** `01-primitives.md` §2 treats volume and shape
as one primitive with two axes, and they share every mechanism — reset cycle,
seeding inside it, cache control, the N=0 baseline, draining lazy results —
because the failure modes they have to survive are identical. Two modules would
mean either duplicating that or importing a private helper across a boundary.

## Consequences

**Makes easy.** Asking *is this cost about how much data there is, or about how
it is shaped* — two questions no single sweep answers. S-7.7's skewed fixture
generation has its allocation arithmetic already written and tested.

**Makes hard.** A subject whose seeding cannot accept a per-parent allocation
gets the volume axis only. That is honest: the shape axis needs a fixture
generator that takes a recipe, and pretending otherwise would produce a
comparison of one shape against itself.

**Rules out.** Comparing shapes at different volumes, a shape that empties
parents, and drawing a growth conclusion without saying what shape it was
measured under.

## Provenance

Three sabotage runs, each asserting the edit was detected: rounding each share
independently instead of by largest remainder fails 12 tests; defining the long
tail with the power law's weights fails 3, including the skew-defect test;
removing the one-child floor fails 23.

Two of this story's own assertions were wrong before they were run, and both
were wrong in the same direction — assuming a shape was more extreme than it
was. The power law's head mass at twenty parents is 0.39, not the 0.5 asserted,
and the skew ratio against uniform is 2.3×, not the 2.5× asserted. The second
failure is what exposed the long-tail definition as a duplicate of the power law;
had the thresholds been guessed slightly lower, both tests would have passed and
the story would have shipped two distributions under three names.
