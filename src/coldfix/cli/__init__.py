"""The command line. **The application layer, and the only one allowed to know both.**

`coldfix.cli.wiring` imports `coldfix.adapters`; nothing else outside `adapters/`
may. See that module for why the layering invariant widens here and nowhere else.

S-17.18.
"""
