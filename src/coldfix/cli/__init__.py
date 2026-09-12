"""The command line — the application layer, and the only place that reads the
environment.

v1 widened the layering invariant here: `cli/wiring.py` was allowed to import
`coldfix.adapters` so the command could resolve a framework adapter, and nothing
else outside `adapters/` could. S-31.1 deleted both, and the exception with them.
What is left assembles the seven nodes from a configuration and invokes the graph.

S-17.18, cut to v3 in S-31.1.
"""
