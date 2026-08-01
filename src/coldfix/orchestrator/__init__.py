"""Graph, state, checkpointing, budgets.

Checkpointed state and the persistent store are deliberately separate: a rewind
must restore the code state without discarding the failure knowledge that
motivated the rewind.

Epics 6 and 12.
"""
