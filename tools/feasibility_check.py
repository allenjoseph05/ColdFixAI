"""Can we measure a program we did not write, without changing it?

Five checks. No agents, no model calls, no spending. Every claim in
`docs/walkthrough/13-v3-collection.md` rests on these working, so they are
worth an hour before anything is built on top of them.

    python tools/feasibility_check.py

By default this runs the checks **inside a Linux container**, which is where the
real system runs them. That is not incidental: on Windows, child-process CPU
time is not reliably readable, `opentelemetry-instrument` breaks on a path
containing a space, and py-spy cannot attach. All three work on Linux.

To run directly on this machine instead (expect Windows failures):

    uv run --with psutil --with py-spy --with opentelemetry-distro \
           --with opentelemetry-instrumentation-sqlite3 \
           python tools/feasibility_check.py --here

A FAIL here is the cheapest possible bad news.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

POLL_SECONDS = 0.01
RUN_TIMEOUT = 300
IMAGE = "python:3.12-slim"
PACKAGES = "psutil py-spy opentelemetry-distro opentelemetry-instrumentation-sqlite3"

# The sample program. A planted N+1 over sqlite3, plus enough string work that a
# sampling profiler has something to see. sqlite3 is used because it is in the
# standard library and OpenTelemetry instruments it, so the check needs no
# database server. The scale is chosen so a run takes a second or two -- a
# faster run is not measurable by sampling, and a slower one wastes the hour.
SAMPLE_APP = '''\
import sqlite3
import sys

SCHEMA = """
CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE books (id INTEGER PRIMARY KEY, author_id INTEGER, title TEXT);
"""


def seed(conn, authors):
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO authors (id, name) VALUES (?, ?)",
        [(i, "author-%d" % i) for i in range(authors)],
    )
    conn.executemany(
        "INSERT INTO books (author_id, title) VALUES (?, ?)",
        [(i, "book-%d-%d" % (i, k)) for i in range(authors) for k in range(20)],
    )
    conn.commit()


def books_for_author(cur, author_id):
    cur.execute("SELECT title FROM books WHERE author_id = ?", (author_id,))
    return [row[0] for row in cur.fetchall()]


def render(name, titles):
    out = []
    for t in titles:
        out.append("%s :: %s" % (name.upper(), t.title()))
    return " | ".join(out)


def main():
    authors = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    conn = sqlite3.connect(":memory:")
    seed(conn, authors)

    cur = conn.cursor()
    cur.execute("SELECT id, name FROM authors")
    rows = cur.fetchall()
    lines = []
    for author_id, name in rows:
        titles = books_for_author(cur, author_id)
        lines.append(render(name, titles))

    # The count the harness cross-checks the span count against. An instrument
    # that reports zero here is an instrument that was not attached.
    print(
        "authors=%d rendered=%d chars=%d queries=%d"
        % (len(rows), len(lines), sum(map(len, lines)), len(rows) + 1)
    )


if __name__ == "__main__":
    main()
'''

# The ablation: the per-author lookup is replaced by a stub that issues no query.
# This deliberately breaks the program, which is the point -- it is how we learn
# what that call cost. It runs in a copy that is deleted afterwards.
ABLATED_FUNCTION = '''\
def books_for_author(cur, author_id):
    return []
'''

ORIGINAL_FUNCTION = '''\
def books_for_author(cur, author_id):
    cur.execute("SELECT title FROM books WHERE author_id = ?", (author_id,))
    return [row[0] for row in cur.fetchall()]
'''


@dataclass
class Measured:
    wall: float
    cpu: float
    peak_rss: int
    read_bytes: int | None
    write_bytes: int | None
    method: str
    returncode: int
    stdout: str
    stderr: str


@dataclass
class Result:
    name: str
    status: str
    detail: str


def _posix_usage() -> tuple[float, int, int, int]:
    import resource

    u = resource.getrusage(resource.RUSAGE_CHILDREN)
    # ru_maxrss is kilobytes on Linux, bytes on macOS.
    scale = 1 if sys.platform == "darwin" else 1024
    return (u.ru_utime + u.ru_stime, u.ru_maxrss * scale, u.ru_inblock, u.ru_oublock)


def run_and_measure(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> Measured:
    """Run a command and take every free measurement while it runs.

    On POSIX this is `getrusage(RUSAGE_CHILDREN)` before and after, which is
    exact and costs nothing. Polling is the Windows fallback and is *not*
    equivalent -- it is here so the script runs at all on a developer machine,
    and the result reports which method produced it.
    """
    merged = dict(os.environ)
    if env:
        merged.update(env)

    posix = hasattr(os, "getpid") and sys.platform != "win32"
    before = _posix_usage() if posix else None

    start = time.perf_counter()
    proc = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=merged,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    peak_rss = 0
    cpu = 0.0
    read_bytes: int | None = None
    write_bytes: int | None = None

    if posix:
        stdout, stderr = proc.communicate(timeout=RUN_TIMEOUT)
        wall = time.perf_counter() - start
        after = _posix_usage()
        assert before is not None
        cpu = after[0] - before[0]
        peak_rss = after[1]
        read_bytes = (after[2] - before[2]) * 512
        write_bytes = (after[3] - before[3]) * 512
        method = "getrusage"
    else:
        import psutil

        try:
            watched = psutil.Process(proc.pid)
        except psutil.NoSuchProcess:
            watched = None
        while proc.poll() is None:
            if watched is not None:
                try:
                    with watched.oneshot():
                        peak_rss = max(peak_rss, watched.memory_info().rss)
                        times = watched.cpu_times()
                        cpu = max(cpu, times.user + times.system)
                        try:
                            counters = watched.io_counters()
                            read_bytes = counters.read_bytes
                            write_bytes = counters.write_bytes
                        except (psutil.AccessDenied, AttributeError):
                            pass
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            time.sleep(POLL_SECONDS)
        stdout, stderr = proc.communicate(timeout=RUN_TIMEOUT)
        wall = time.perf_counter() - start
        method = "psutil polling"

    return Measured(
        wall, cpu, peak_rss, read_bytes, write_bytes, method, proc.returncode, stdout, stderr
    )


def on_path(binary: str) -> bool:
    return shutil.which(binary) is not None


# ----------------------------------------------------------------- the checks


def check_clocks(command: list[str], cwd: Path) -> Result:
    """Elapsed time and processor time, and whether they can be told apart."""
    m = run_and_measure(command, cwd)
    if m.returncode != 0:
        return Result("1. clocks", "FAIL", f"target exited {m.returncode}: {m.stderr[:200]}")
    if m.wall <= 0:
        return Result("1. clocks", "FAIL", "no elapsed time measured")
    if m.cpu <= 0:
        return Result("1. clocks", "FAIL", f"elapsed {m.wall:.2f}s but processor time read 0 ({m.method})")

    ratio = m.cpu / m.wall
    mode = "computing" if ratio > 0.7 else "waiting"
    if ratio < 0.2 and m.method != "getrusage":
        return Result(
            "1. clocks",
            "FAIL",
            f"elapsed {m.wall:.2f}s, processor {m.cpu:.2f}s -- implausible for a busy program; "
            f"{m.method} is undercounting",
        )
    return Result(
        "1. clocks",
        "PASS",
        f"elapsed {m.wall:.2f}s, processor {m.cpu:.2f}s, ratio {ratio:.2f} -> {mode} ({m.method})",
    )


def check_os_counters(command: list[str], cwd: Path) -> Result:
    """Peak memory and disk traffic, taken from outside the process."""
    m = run_and_measure(command, cwd)
    if m.peak_rss <= 0:
        return Result("2. os counters", "FAIL", f"peak memory read 0 ({m.method})")
    mb = m.peak_rss / (1024 * 1024)
    io = "io counters unavailable"
    if m.read_bytes is not None:
        io = f"read {m.read_bytes / 1024:.0f}KB, wrote {(m.write_bytes or 0) / 1024:.0f}KB"
    return Result("2. os counters", "PASS", f"peak memory {mb:.0f}MB, {io} ({m.method})")


def check_otel(command: list[str], cwd: Path) -> Result:
    """Database spans, via a launch wrapper, with no change to the target.

    The span count is cross-checked against a count the program reports itself.
    Without that guard a zero is indistinguishable from a program that issues no
    queries -- which is exactly how a measurement system confidently announces
    "nothing found". This check found that blind spot for real: OpenTelemetry's
    sqlite3 instrumentation wraps `cursor.execute` and never sees the
    `connection.execute` shortcut, so an app using the shortcut reports zero
    queries while issuing thousands.
    """
    if not on_path("opentelemetry-instrument"):
        return Result("3. otel spans", "SKIP", "opentelemetry-instrument not on PATH")

    wrapped = [
        "opentelemetry-instrument",
        "--traces_exporter",
        "console",
        "--metrics_exporter",
        "none",
        "--logs_exporter",
        "none",
        *command,
    ]
    m = run_and_measure(wrapped, cwd, env={"OTEL_SERVICE_NAME": "feasibility-subject"})
    blob = m.stdout + m.stderr
    if m.returncode != 0:
        return Result("3. otel spans", "FAIL", f"wrapper exited {m.returncode}: {blob[-300:]}")

    spans = blob.count('"name":')
    db_spans = blob.count("db.system")

    expected = None
    for token in blob.split():
        if token.startswith("queries="):
            expected = int(token.split("=", 1)[1])
            break

    if spans == 0:
        return Result(
            "3. otel spans",
            "FAIL",
            "wrapper ran but emitted no spans -- no instrumentation package matched this program",
        )
    if db_spans == 0:
        return Result(
            "3. otel spans",
            "FAIL",
            f"{spans} spans emitted, none carrying db.system -- the instrument is attached "
            "but blind to how this program calls the database",
        )
    if expected is not None and db_spans < expected * 0.8:
        return Result(
            "3. otel spans",
            "PARTIAL",
            f"{db_spans} db spans but the program reports {expected} queries -- the instrument "
            "is missing some call path",
        )
    detail = f"{spans} spans, {db_spans} carrying db.system with statement text"
    if expected is not None:
        detail += f"; cross-checks against the program's own count of {expected}"
    return Result("3. otel spans", "PASS", detail)


def check_pyspy(command: list[str], cwd: Path, out_dir: Path) -> Result:
    """Stacks with file and line numbers, by launching under the profiler.

    Launching rather than attaching is deliberate: attaching to a running
    process needs SYS_PTRACE, which Docker drops by default, while the `--`
    form needs no elevated permission.
    """
    if not on_path("py-spy"):
        return Result("4. stacks", "SKIP", "py-spy not on PATH")

    target = out_dir / "profile.json"
    wrapped = ["py-spy", "record", "-o", str(target), "-f", "speedscope", "--", *command]
    m = run_and_measure(wrapped, cwd)
    if not target.exists():
        return Result("4. stacks", "FAIL", f"py-spy produced no profile: {(m.stderr or m.stdout)[-300:]}")

    text = target.read_text(encoding="utf-8", errors="replace")
    if '"file"' not in text or '"line"' not in text:
        return Result("4. stacks", "PARTIAL", "profile captured but carries no file/line fields")
    named = [n for n in ("books_for_author", "render", "seed") if n in text]
    detail = f"{target.stat().st_size // 1024}KB profile with file and line fields"
    if named:
        detail += f"; names the planted functions {named}"
    return Result("4. stacks", "PASS", detail)


def check_ablation(command: list[str], cwd: Path, app_file: Path, work: Path) -> Result:
    """Delete the suspect work in a throwaway copy and see whether cost moves."""
    if not app_file.exists():
        return Result("5. ablation", "SKIP", "only runs against the built-in sample")

    baseline = run_and_measure(command, cwd)
    if baseline.returncode != 0:
        return Result("5. ablation", "FAIL", "baseline run failed")

    copy_root = work / "ablated"
    if copy_root.exists():
        shutil.rmtree(copy_root)
    shutil.copytree(cwd, copy_root)

    copied = copy_root / app_file.name
    source = copied.read_text(encoding="utf-8")
    if ORIGINAL_FUNCTION not in source:
        return Result("5. ablation", "FAIL", "could not locate the function to stub")
    copied.write_text(source.replace(ORIGINAL_FUNCTION, ABLATED_FUNCTION), encoding="utf-8")

    stubbed = run_and_measure(command, copy_root)
    if stubbed.returncode != 0:
        return Result("5. ablation", "FAIL", f"stubbed run failed: {stubbed.stderr[:200]}")

    if baseline.stdout.strip() == stubbed.stdout.strip():
        return Result(
            "5. ablation",
            "PARTIAL",
            "the stub ran but the output did not change -- the harness cannot tell the copy apart",
        )
    saved = baseline.wall - stubbed.wall
    share = saved / baseline.wall if baseline.wall else 0.0
    return Result(
        "5. ablation",
        "PASS",
        f"stubbing one function moved elapsed {baseline.wall:.2f}s -> {stubbed.wall:.2f}s "
        f"({share * 100:.0f}% of the run), and the output changed as expected",
    )


# ------------------------------------------------------------------- driver


def build_sample(work: Path) -> tuple[Path, Path, list[str], list[str]]:
    """The sample, plus two scales.

    The span check runs at a much smaller scale on purpose: counting queries per
    item needs a hundred of them, not three thousand, and the console exporter
    prints every span it sees.
    """
    app_dir = work / "sample"
    app_dir.mkdir(parents=True, exist_ok=True)
    app_file = app_dir / "app.py"
    app_file.write_text(SAMPLE_APP, encoding="utf-8")
    return app_dir, app_file, [sys.executable, "app.py", "3000"], [sys.executable, "app.py", "150"]


def run_in_container() -> int:
    """Re-run this same file inside a Linux container, which is the real target."""
    here = Path(__file__).resolve().parent
    print(f"running the checks inside {IMAGE} (this is where the real system measures)\n")
    command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{here}:/tools:ro",
        IMAGE,
        "bash",
        "-lc",
        f"pip install --quiet --root-user-action=ignore {PACKAGES} "
        "&& python /tools/feasibility_check.py --here",
    ]
    completed = subprocess.run(command, text=True)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--here", action="store_true", help="run on this machine, not in a container")
    parser.add_argument("--dir", type=Path, help="a real repository to measure instead of the sample")
    parser.add_argument("--command", help="how to run it, e.g. 'python -m app'")
    args = parser.parse_args()

    if not args.here:
        if not on_path("docker"):
            print("docker not found. Start Docker Desktop, or use --here to run on this machine.")
            return 2
        return run_in_container()

    work = Path(tempfile.mkdtemp(prefix="coldfix-feasibility-"))
    try:
        if args.dir:
            if not args.command:
                print("--dir requires --command")
                return 2
            cwd = args.dir.resolve()
            command = args.command.split()
            light = command
            app_file = Path("does-not-exist")
            print(f"target: {cwd}  ({args.command})\n")
        else:
            cwd, app_file, command, light = build_sample(work)
            print(f"target: built-in sample with a planted N+1\nplatform: {sys.platform}\n")

        results = [
            check_clocks(command, cwd),
            check_os_counters(command, cwd),
            check_otel(light, cwd),
            check_pyspy(command, cwd, work),
            check_ablation(command, cwd, app_file, work),
        ]

        width = max(len(r.name) for r in results)
        print("=" * 78)
        for r in results:
            print(f"{r.name:<{width}}  {r.status:<8}  {r.detail}")
        print("=" * 78)

        failed = [r for r in results if r.status == "FAIL"]
        partial = [r for r in results if r.status in {"PARTIAL", "SKIP"}]
        print()
        if failed:
            print(f"{len(failed)} FAILED. The design rests on these; fix or drop them before building.")
        elif partial:
            print(f"No failures. {len(partial)} incomplete -- worth understanding before relying on them.")
        else:
            print("All five work. Everything in 13-v3-collection.md is buildable.")
        return 1 if failed else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
