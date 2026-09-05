"""S-18.5's acceptance, against a real instrument that really goes blind.

Two programs that issue exactly the same queries by two different routes:

  visible    cursor.execute(...)       -- OpenTelemetry wraps this
  invisible  connection.execute(...)   -- the shortcut, which builds its cursor
                                          down in C where the wrapper never sees it

The second is the failure this whole project is built to refuse, and it is not
hypothetical: it was the first thing tried on 2026-09-05 and it reported zero
spans for three thousand queries.

Run inside the corpus image. Exits non-zero if the guard fails to catch it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/repo/src")

from coldfix.collect.attachment import (  # noqa: E402
    InstrumentNotAttachedError,
    expect_from_report,
    verify_attached,
)

PROGRAM = """\
import sqlite3, sys

conn = sqlite3.connect(":memory:")
{opener}
{setup}("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
for i in range(120):
    {setup}("INSERT INTO t VALUES (?, ?)", (i, "x"))
for i in range(120):
    {setup}("SELECT v FROM t WHERE id = ?", (i,)).fetchall()
print("rows=120 queries=241")
"""

# `expect` is explicit rather than derived from the label: "visible (" is a
# substring of "invisible (", and the first version of this file matched on it
# and declared a correct refusal wrong.
ROUTES = (
    ("cursor.execute -- the route OpenTelemetry wraps",
     {"opener": "cur = conn.cursor()", "setup": "cur.execute"}, "ACCEPTED"),
    ("connection.execute -- the shortcut, cursor built in C",
     {"opener": "", "setup": "conn.execute"}, "REFUSED"),
)

work = Path(tempfile.mkdtemp())
failures = 0
print()

for index, (label, shape, expected) in enumerate(ROUTES):
    source = work / f"subject{index}.py"
    source.write_text(PROGRAM.format(**shape), encoding="utf-8")

    completed = subprocess.run(
        [
            "opentelemetry-instrument",
            "--traces_exporter",
            "console",
            "--metrics_exporter",
            "none",
            "--logs_exporter",
            "none",
            sys.executable,
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    blob = completed.stdout + completed.stderr
    spans = blob.count('"db.system"')
    expectation = expect_from_report(blob, metric="db_query", reported_as="queries")

    print(f"{label}")
    print(f"    spans carrying db.system: {spans}")
    print(f"    the program says: queries={expectation.at_least if expectation else 'nothing'}")

    try:
        verify_attached({"db_query": spans}, [expectation] if expectation else [])
        verdict = "ACCEPTED"
    except InstrumentNotAttachedError as caught:
        verdict = f"REFUSED  {type(caught).__name__}"
    except Exception as caught:  # noqa: BLE001 - the partial case is also a refusal
        verdict = f"REFUSED  {type(caught).__name__}"

    ok = verdict.startswith(expected)
    failures += 0 if ok else 1
    print(f"    {verdict}   {'as expected' if ok else 'WRONG -- expected ' + expected}\n")

print(
    "the guard caught the blind instrument"
    if failures == 0
    else f"{failures} case(s) went the wrong way"
)
raise SystemExit(1 if failures else 0)
