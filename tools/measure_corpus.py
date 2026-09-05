"""S-18.1's acceptance: the real `measure` against every corpus subject.

Not the feasibility probe -- the actual tool, returning the actual artifact.
Runs inside the corpus image, where Linux, `getrusage` and the corpus packages
all exist.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/tools")
sys.path.insert(0, "/repo/src")

from corpus import CORPUS  # noqa: E402

from coldfix.collect.measurement import MeasurementError, measure  # noqa: E402

rows: list[tuple[str, str, str]] = []
work = Path(tempfile.mkdtemp())

for subject in CORPUS:
    cwd = work / subject.name
    cwd.mkdir(parents=True, exist_ok=True)
    (cwd / "drive.py").write_text(subject.driver, encoding="utf-8")
    command = [sys.executable, "drive.py", str(subject.scale)]
    try:
        m = measure(command, cwd=cwd, repeats=3)
        detail = (
            f"{m.wall.median:6.2f}s wall  {m.cpu_s:6.2f}s cpu  {m.mode.value:<9} "
            f"peak {(m.peak_rss_bytes or 0) // (1024 * 1024):4d}MB  "
            f"floor {m.noise_floor_s * 1000:5.0f}ms  {m.measurement_id}"
        )
        rows.append((subject.name, "MEASURED", detail))
    except MeasurementError as refused:
        rows.append((subject.name, type(refused).__name__, str(refused).splitlines()[0][:96]))

width = max(len(n) for n, _, _ in rows)
print()
for name, status, detail in rows:
    print(f"{name:<{width}}  {status:<22}  {detail}")
print()
measured = sum(1 for _, s, _ in rows if s == "MEASURED")
print(f"{measured} of {len(rows)} measured; the rest refused with a named reason.")
