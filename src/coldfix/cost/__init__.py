"""What each model call cost, and what a run cost per finding.

Epic 5, S-5.3. `04-cost.md` §12 puts the gap between the worst case and the
engineered case at ~60x — roughly €125,000 against €2,150 — and every technique
that closes it is measured in tokens. None of it is checkable without a ledger,
so this is where the numbers in that document stop being estimates.

Nothing here calls a model. It records what a call used and prices it.
"""
