"""S-18.3's acceptance: the real `profile` against every corpus subject.

Runs inside the corpus image, where py-spy and the corpus packages exist. The
two subjects expected to behave badly -- `click`, which barely runs, and `lxml`,
whose work happens in C -- are the point of the exercise.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/tools")
sys.path.insert(0, "/repo/src")

from corpus import CORPUS  # noqa: E402

from coldfix.collect.measurement import MeasurementError  # noqa: E402
from coldfix.collect.profiling import profile  # noqa: E402

work = Path(tempfile.mkdtemp())

for subject in CORPUS:
    cwd = work / subject.name
    cwd.mkdir(parents=True, exist_ok=True)
    (cwd / "drive.py").write_text(subject.driver, encoding="utf-8")
    command = [sys.executable, "drive.py", str(subject.scale)]
    print(f"\n=== {subject.name}  ({subject.shape})")
    try:
        result = profile(command, cwd=cwd, top=3)
    except MeasurementError as refused:
        print(f"    REFUSED  {type(refused).__name__}: {str(refused).splitlines()[0][:90]}")
        continue

    counts = result.counts
    print(
        f"    {counts['samples']} samples, "
        f"{counts['attributed_samples']} attributed, "
        f"{counts['unattributable_samples']} unattributable"
    )
    for site in result.sites:
        where = Path(site.file).name
        via = site.call_path[-1].split("/")[-1] if site.call_path else "-"
        print(
            f"    {site.self_share * 100:5.1f}%  {where}:{site.line:<5} "
            f"{site.symbol:<24} via {via}"
        )
    for gap in result.not_measured:
        print(f"    NOT MEASURED  {gap.what}: {gap.why}")
