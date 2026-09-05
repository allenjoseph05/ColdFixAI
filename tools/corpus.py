"""The test corpus: real packages, deliberately different shapes.

S-18.0. Every tool in E18 is validated against all of these, not against one
planted fixture. The point is to meet the edge cases now -- a C extension the
profiler cannot see into, a program dominated by import time, a workload with no
database at all -- rather than after the tool has been built around one example.

Chosen for shape, not popularity:

  planted   a known N+1 over sqlite       -- the control; we know the answer
  clean     a tight numeric loop          -- the negative control; nothing to find
  pygments  pure-Python CPU work          -- deep call stacks, no database
  jinja2    pure-Python CPU work          -- compiled templates, different profile
  sqlparse  pure-Python parsing           -- string and allocation heavy
  markdown  pure-Python parsing           -- regex heavy
  click     import-dominated              -- almost no runtime work at all
  lxml      a C extension                 -- the profiler cannot see inside it

`click` and `lxml` are the ones expected to behave badly. They are in the corpus
because a tool that only works on the friendly cases is not finished.

Every driver prints `queries=N` so the instrument-attachment guard (S-18.5) has
a witness that would disagree with a false zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Subject:
    name: str
    shape: str
    packages: tuple[str, ...]
    driver: str
    scale: int
    # The function whose removal should remove the work, and the constant to
    # put in its place. `None` where no single definition carries it -- which
    # is itself a fact about that subject rather than something to work around.
    ablation: tuple[str, str] | None = None
    # Stated up front so a surprise is visible rather than absorbed.
    expects: dict[str, object] = field(default_factory=dict)


PLANTED = '''\
import sqlite3, sys

def seed(cur, authors):
    cur.executescript("CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT);"
                      "CREATE TABLE books (id INTEGER PRIMARY KEY, author_id INTEGER, title TEXT);")
    cur.executemany("INSERT INTO authors (id, name) VALUES (?, ?)",
                    [(i, "author-%d" % i) for i in range(authors)])
    cur.executemany("INSERT INTO books (author_id, title) VALUES (?, ?)",
                    [(i, "book-%d-%d" % (i, k)) for i in range(authors) for k in range(20)])

def books_for_author(cur, author_id):
    return [r[0] for r in cur.execute("SELECT title FROM books WHERE author_id = ?", (author_id,)).fetchall()]

def render(name, titles):
    return " | ".join("%s :: %s" % (name.upper(), t.title()) for t in titles)

def main():
    n = int(sys.argv[1])
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    seed(cur, n)
    rows = cur.execute("SELECT id, name FROM authors").fetchall()
    lines = [render(name, books_for_author(cur, aid)) for aid, name in rows]
    print("authors=%d chars=%d queries=%d" % (len(rows), sum(map(len, lines)), len(rows) + 1))

main()
'''

CLEAN = '''\
import sys
def work(i):
    return i * i

n = int(sys.argv[1])
total = 0
for i in range(n):
    total += work(i)
print("total=%d queries=0" % total)
'''

PYGMENTS = '''\
import sys, os, pygments
from pygments import highlight
from pygments.lexers import PythonLexer
from pygments.formatters import HtmlFormatter

source = open(os.path.join(os.path.dirname(pygments.__file__), "lexer.py")).read()
lexer, fmt = PythonLexer(), HtmlFormatter()

def work():
    return len(highlight(source, lexer, fmt))

n = int(sys.argv[1])
size = 0
for _ in range(n):
    size += work()
print("rendered=%d chars=%d queries=0" % (n, size))
'''

JINJA = '''\
import sys
from jinja2 import Template
t = Template("{% for i in items %}<li>{{ i }}-{{ loop.index }}</li>{% endfor %}")

def work():
    return len(t.render(items=range(400)))

n = int(sys.argv[1])
size = 0
for _ in range(n):
    size += work()
print("rendered=%d chars=%d queries=0" % (n, size))
'''

SQLPARSE = '''\
import sys, sqlparse
sql = "SELECT a.x, b.y FROM a JOIN b ON a.id = b.a_id WHERE a.k = 1 AND b.k = 2; " * 40
def work():
    return len(sqlparse.format(sql, reindent=True, keyword_case="upper"))

n = int(sys.argv[1])
size = 0
for _ in range(n):
    size += work()
print("parsed=%d chars=%d queries=0" % (n, size))
'''

MARKDOWN = '''\
import sys, markdown
text = "# Title\\n\\nSome *emphasis* and `code` and [a link](http://x).\\n\\n- one\\n- two\\n\\n" * 120
def work():
    return len(markdown.markdown(text))

n = int(sys.argv[1])
size = 0
for _ in range(n):
    size += work()
print("rendered=%d chars=%d queries=0" % (n, size))
'''

CLICK = '''\
import sys, click

@click.command()
@click.option("--count", default=1)
def cli(count):
    click.echo("count=%d" % count)

print("imported=1 queries=0")
'''

LXML = '''\
import sys
from lxml import etree
doc = "<root>" + "".join("<item id='%d'><v>%d</v></item>" % (i, i) for i in range(400)) + "</root>"
def work():
    return len(etree.fromstring(doc.encode()).findall(".//item"))

n = int(sys.argv[1])
total = 0
for _ in range(n):
    total += work()
print("parsed=%d items=%d queries=0" % (n, total))
'''


CORPUS: tuple[Subject, ...] = (
    Subject(
        name="planted",
        shape="database, known N+1",
        packages=(),
        driver=PLANTED,
        scale=2000,
        ablation=("books_for_author", "[]"),
        expects={"mode": "computing", "db_queries": ">100", "ablation_moves": True},
    ),
    Subject(
        name="clean",
        shape="negative control, nothing to find",
        packages=(),
        driver=CLEAN,
        scale=4_000_000,
        ablation=("work", "0"),
        expects={"mode": "computing", "db_queries": 0, "ablation_moves": True},
    ),
    Subject(
        name="pygments",
        shape="pure-Python CPU, deep stacks",
        packages=("pygments",),
        driver=PYGMENTS,
        scale=12,
        ablation=("work", "0"),
        expects={"mode": "computing", "profile_names_library": True},
    ),
    Subject(
        name="jinja2",
        shape="pure-Python CPU, compiled templates",
        packages=("jinja2",),
        driver=JINJA,
        scale=900,
        ablation=("work", "0"),
        expects={"mode": "computing", "profile_names_library": True},
    ),
    Subject(
        name="sqlparse",
        shape="string and allocation heavy",
        packages=("sqlparse",),
        driver=SQLPARSE,
        scale=120,
        ablation=("work", "0"),
        expects={"mode": "computing"},
    ),
    Subject(
        name="markdown",
        shape="regex heavy",
        packages=("markdown",),
        driver=MARKDOWN,
        scale=40,
        ablation=("work", "0"),
        expects={"mode": "computing"},
    ),
    Subject(
        name="click",
        shape="import-dominated, almost no runtime work",
        packages=("click",),
        driver=CLICK,
        scale=1,
        expects={"startup_dominates": True, "profile_may_be_empty": True},
    ),
    Subject(
        name="lxml",
        shape="C extension, profiler cannot see inside",
        packages=("lxml",),
        driver=LXML,
        scale=400,
        ablation=("work", "0"),
        expects={"mode": "computing", "profile_stops_at_boundary": True},
    ),
)


def packages() -> list[str]:
    """Everything the corpus image needs, deduplicated and ordered."""
    seen: list[str] = []
    for subject in CORPUS:
        for package in subject.packages:
            if package not in seen:
                seen.append(package)
    return seen
