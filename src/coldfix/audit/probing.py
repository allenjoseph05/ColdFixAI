"""The script that drives one workload with one adversarial input. **`Resources.probe`.**

S-17.13. S-10.2 compares what a workload returns before and after a patch, and it
needs something that turns *an input* into *an output*. `Probe` is that thing, and
until now nothing in `src/` built one — the attack existed and could not reach a
subject.

**`coldfix_input` is fixture data, not request parameters.** Read off the seven
classes the attack sweeps: `EMPTY`, `NULL`, `DUPLICATES`, `TIES`, `UNICODE`,
`BOUNDARY`, `UNORDERED`. Every one is a property of the rows a workload reads and
none is a property of a query string, so the script seeds the subject with the
input and *then* drives the route. A probe that passed the payload as `?params`
would be testing the router.

**The wrapper is `equivalence.harness()` and this module does not touch it.** That
function embeds the payload `ensure_ascii=True`, compiles inside its guarded block,
and refuses a script that binds nothing. What was missing is the `script` — the
half that knows the settings module, the model and the route, which is the
subject's business and therefore the campaign's to supply.

**Nothing here is written into the subject's tree.** The script travels on the
command line, which is S-10.2's rule for S-2.4's reason: a patch touching a
protected path is rejected, so a probe materialised as a file would be a protected
path every later diff shows.

**The script deletes before it seeds, and that is deliberate.** It is what makes
`EMPTY` a testable class rather than a hypothetical one: an input of `[]` has to
produce a workload reading nothing. It runs inside a candidate session — a
throwaway container over a throwaway worktree, against a database S-2.5 refuses to
open if its URL looks like production. The same three lines pointed anywhere else
would be the worst code in this repository, and the sandbox is the only reason
they are not.
"""

from __future__ import annotations

from coldfix.audit.equivalence import EquivalenceError, Probe

_TEMPLATE = """
import json

import django

django.setup()

from django.apps import apps
from django.test import Client

model = apps.get_model({label!r})

# Delete first. An input of `[]` has to mean *the workload reads nothing*, and a
# probe that only ever added rows could never produce the empty case the attack
# sweeps for.
model.objects.all().delete()
for row in (coldfix_input or []):
    model.objects.create(**row)

response = Client().get({path!r})

# The status travels beside the body because they are different observations. A
# patch that turns a 200 into a 500 returns no body at all, and a comparison of
# two absent bodies is two runs agreeing about nothing.
body = response.content.decode("utf-8", "replace")
try:
    parsed = json.loads(body)
except ValueError:
    parsed = body

output = {{"status": int(response.status_code), "body": parsed}}
"""


def probe_for(workload: str, *, path: str, model: str, settings: str) -> Probe:
    """A probe that seeds `model` from the input and requests `path`.

    `settings` is supplied rather than detected for `grounder_for`'s reason: a
    probe run against a guessed configuration that happens to import would compare
    two revisions of the wrong application, and both would agree.

    `model` is a Django app label — `"shop.Book"` — because `apps.get_model` takes
    one and a probe that guessed the app from the model name would seed a
    different table than the route reads.

    Raises:
        EquivalenceError: `path`, `model` or `settings` is empty. Refused here
            rather than at the run, because a probe missing any of the three
            produces the same absence of output on both revisions, and the attack
            reads that as the patch surviving.
    """
    missing = [
        name
        for name, value in (("path", path), ("model", model), ("settings", settings))
        if not value.strip()
    ]
    if missing:
        message = (
            f"a probe for {workload} needs {missing}. Without them both revisions fail the same "
            "way and produce the same absence of output, which S-10.2 reads as the patch "
            "surviving its attack"
        )
        raise EquivalenceError(message)

    return Probe(
        workload=workload,
        # `DJANGO_SETTINGS_MODULE` is set in the environment by the caller that
        # runs this, not here: `Surface.run` takes overrides and the subject's
        # settings are one, so a script exporting its own would be a second place
        # the configuration is stated.
        script=f"import os\nos.environ.setdefault('DJANGO_SETTINGS_MODULE', {settings!r})\n"
        + _TEMPLATE.format(label=model, path=path),
    )
