"""S-18.4's acceptance: the real `ablate` against every corpus subject."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/tools")
sys.path.insert(0, "/repo/src")

from corpus import CORPUS  # noqa: E402

from coldfix.collect.ablation import ablate  # noqa: E402
from coldfix.collect.measurement import MeasurementError  # noqa: E402

work = Path(tempfile.mkdtemp())
print()
for subject in CORPUS:
    if subject.ablation is None:
        print(f"{subject.name:9}  SKIP     no single definition carries this subject's work")
        continue
    cwd = work / subject.name
    cwd.mkdir(parents=True, exist_ok=True)
    (cwd / "drive.py").write_text(subject.driver, encoding="utf-8")
    symbol, returns = subject.ablation
    try:
        r = ablate(
            [sys.executable, "drive.py", str(subject.scale)],
            cwd=cwd,
            path="drive.py",
            symbol=symbol,
            returns=returns,
            repeats=3,
        )
        changed = "output changed" if r.output_changed else "OUTPUT UNCHANGED"
        print(
            f"{subject.name:9}  {r.share_removed * 100:5.1f}% removed  "
            f"{r.before.wall.median:5.2f}s -> {r.after.wall.median:5.2f}s  "
            f"{symbol}:{r.line:<4} {changed}"
        )
    except MeasurementError as refused:
        print(f"{subject.name:9}  {type(refused).__name__}: {str(refused).splitlines()[0][:70]}")
