"""Run every check against every subject in the corpus.

S-18.0. `feasibility_check.py` proved the five collectors work on one planted
program. This proves they work -- or says exactly where they do not -- across
eight programs of deliberately different shapes.

    python tools/corpus_check.py

It builds one Linux image holding every corpus package plus the tools, then runs
the matrix inside it. A cell that fails here is an edge case found before any
code is built on top of it, which is the entire point.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus import CORPUS, Subject, packages  # noqa: E402
from feasibility_check import (  # noqa: E402
    check_clocks,
    check_os_counters,
    check_otel,
    check_pyspy,
    on_path,
    run_and_measure,
)

IMAGE_TAG = "coldfix-corpus:1"
TOOL_PACKAGES = "psutil py-spy opentelemetry-distro opentelemetry-instrumentation-sqlite3"

DOCKERFILE = """\
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends gcc \\
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --root-user-action=ignore {tools} {subjects}
"""


def check_otel_for_subject(subject: Subject, command: list[str], cwd: Path):
    """Spans, judged against whether this subject has a database at all.

    The corpus corrected the guard as first specified. `check_otel` treats zero
    spans as a failure, which is right for a program that talks to a database
    and wrong for the six here that do not -- it fired on every one of them.

    **The witness for a false zero is a declared dependency, not the zero
    itself.** No database declared, no spans expected, and the absence is the
    correct answer rather than a broken instrument. With a database declared, a
    zero means the instrument is not attached and the run must fail.
    """
    from feasibility_check import Result

    expected = subject.expects.get("db_queries", 0)
    wants_db = expected != 0

    result = check_otel(command, cwd)
    if wants_db:
        return result
    if result.status == "PASS":
        return Result(
            "otel", "PARTIAL", f"no database declared, yet spans carry db.system: {result.detail}"
        )
    return Result("otel", "N/A", "no database dependency -- zero spans is the correct answer")


def check_ablation(subject: Subject, command: list[str], cwd: Path, work: Path):
    """Remove the one line that should carry the work, and see whether it did.

    Returns a Result-shaped tuple so the matrix can print it uniformly.
    """
    from feasibility_check import Result

    if subject.ablation is None:
        return Result("ablate", "SKIP", "no single line carries this subject's work")

    baseline = run_and_measure(command, cwd)
    if baseline.returncode != 0:
        return Result("ablate", "FAIL", f"baseline failed: {baseline.stderr[:160]}")

    copy_root = work / f"ablated-{subject.name}"
    if copy_root.exists():
        shutil.rmtree(copy_root)
    shutil.copytree(cwd, copy_root)

    from coldfix.collect.ablation import stub_source

    driver = copy_root / "drive.py"
    symbol, returns = subject.ablation
    stubbed, _ = stub_source(driver.read_text(encoding="utf-8"), symbol, returns)
    driver.write_text(stubbed, encoding="utf-8")

    stubbed = run_and_measure(command, copy_root)
    if stubbed.returncode != 0:
        return Result("ablate", "FAIL", f"stubbed run failed: {stubbed.stderr[:160]}")

    share = (baseline.wall - stubbed.wall) / baseline.wall if baseline.wall else 0.0
    changed = baseline.stdout.strip() != stubbed.stdout.strip()
    expected = bool(subject.expects.get("ablation_moves", True))

    if not changed:
        return Result("ablate", "FAIL", "output did not change -- the stub had no effect")
    if expected and share < 0.10:
        return Result(
            "ablate", "PARTIAL", f"output changed but only {share * 100:.0f}% of the run moved"
        )
    return Result("ablate", "PASS", f"{baseline.wall:.2f}s -> {stubbed.wall:.2f}s ({share * 100:.0f}%)")


def run_subject(subject: Subject, work: Path) -> list:
    from feasibility_check import Result

    cwd = work / subject.name
    cwd.mkdir(parents=True, exist_ok=True)
    (cwd / "drive.py").write_text(subject.driver, encoding="utf-8")

    command = [sys.executable, "drive.py", str(subject.scale)]
    smoke = run_and_measure(command, cwd)
    if smoke.returncode != 0:
        note = (smoke.stderr or smoke.stdout).strip().splitlines()[-1:] or ["no output"]
        return [Result(n, "BLOCKED", note[0][:120]) for n in ("clocks", "os", "otel", "stacks", "ablate")]

    # The OTel pass runs at a smaller scale: counting queries per item needs a
    # hundred of them, not thousands, and the console exporter prints every span.
    light_scale = max(1, min(subject.scale, 150))
    light = [sys.executable, "drive.py", str(light_scale)]

    return [
        check_clocks(command, cwd),
        check_os_counters(command, cwd),
        check_otel_for_subject(subject, light, cwd),
        check_pyspy(command, cwd, work),
        check_ablation(subject, command, cwd, work),
    ]


def build_image() -> int:
    build_dir = Path(tempfile.mkdtemp(prefix="coldfix-corpus-build-"))
    try:
        (build_dir / "Dockerfile").write_text(
            DOCKERFILE.format(tools=TOOL_PACKAGES, subjects=" ".join(packages())),
            encoding="utf-8",
        )
        print(f"building {IMAGE_TAG} with {len(packages())} corpus packages ...")
        completed = subprocess.run(
            ["docker", "build", "-q", "-t", IMAGE_TAG, str(build_dir)],
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            print(completed.stderr[-2000:])
        return completed.returncode
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


def run_in_container() -> int:
    if build_image() != 0:
        return 2
    here = Path(__file__).resolve().parent
    return subprocess.run(
        ["docker", "run", "--rm", "-v", f"{here}:/tools:ro", IMAGE_TAG,
         "python", "/tools/corpus_check.py", "--here"],
        text=True,
    ).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--here", action="store_true", help="run in this environment, not a container")
    parser.add_argument("--only", help="one subject name")
    args = parser.parse_args()

    if not args.here:
        if not on_path("docker"):
            print("docker not found. Start Docker Desktop, or use --here.")
            return 2
        return run_in_container()

    subjects = [s for s in CORPUS if args.only in (None, s.name)]
    work = Path(tempfile.mkdtemp(prefix="coldfix-corpus-"))
    rows: list[tuple[Subject, list]] = []
    try:
        for subject in subjects:
            print(f"  {subject.name} ...", flush=True)
            rows.append((subject, run_subject(subject, work)))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    checks = ["clocks", "os", "otel", "stacks", "ablate"]
    width = max(len(s.name) for s, _ in rows)
    print()
    print("=" * 96)
    print(f"{'subject':<{width}}  " + "  ".join(f"{c:<9}" for c in checks) + "  shape")
    print("-" * 96)
    for subject, results in rows:
        cells = "  ".join(f"{r.status:<9}" for r in results)
        print(f"{subject.name:<{width}}  {cells}  {subject.shape}")
    print("=" * 96)

    print("\ndetail for anything not PASS:")
    quiet = True
    for subject, results in rows:
        for r in results:
            if r.status != "PASS":
                quiet = False
                print(f"  {subject.name}/{r.name}: {r.status} -- {r.detail}")
    if quiet:
        print("  (nothing)")

    failed = sum(1 for _, rs in rows for r in rs if r.status in {"FAIL", "BLOCKED"})
    print()
    print(f"{failed} cell(s) failed out of {len(rows) * len(checks)}.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
