"""The state an investigation checkpoints, and the rules for changing it.

Epic 6, S-6.1. `08-audit.md` F5 splits state in two: what a rewind may discard
(this package) and what it must not (S-6.2's persistent store). The split is the
whole reason the epic exists — time travel restores the position that preceded a
failure, and the record of that failure lives in the state being discarded, so a
naive rewind makes the agent repeat the attempt it rewound to avoid.
"""
