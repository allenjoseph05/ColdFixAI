# S-0.4 — Ablation spike

Does ablation produce deltas that are separable from measurement noise?

Results and the reasoning behind them are in [`FINDINGS.md`](FINDINGS.md).
**Short answer: yes, by about fifty times the detection floor — but timing alone
would have produced a wrong conclusion about stub strategy, and the guard
counters are the only reason it did not.**

Subject: `django-helpdesk` pinned at `3a22901`, the commit S-0.3 grounded, whose
`/api/tickets/` endpoint has a real unplanted N+1.

---

## Run it

```bash
cd spikes/S-0.4-ablation
docker compose up -d

git clone https://github.com/django-helpdesk/django-helpdesk.git repo/django-helpdesk
git -C repo/django-helpdesk checkout 3a2290172ced5bcae9c211ad6ec23cfbc48dcc4e

docker compose exec workbench bash -c 'cd /repo/django-helpdesk && pip install -e . "psycopg[binary]"'
```

Then migrate, load the shipped fixture, and multiply it:

```bash
export E='-e PYTHONPATH=/seeds:/harness -e DJANGO_SETTINGS_MODULE=spike_settings'
docker compose exec $E workbench bash -c 'cd /repo/django-helpdesk && python manage.py migrate'
docker compose exec $E workbench bash -c 'cd /repo/django-helpdesk && python manage.py loaddata demodesk/fixtures/demo.json'
docker compose exec $E workbench bash -c 'cd /repo/django-helpdesk && python manage.py shell < /seeds/scale_helpdesk.py'
```

The fixture is authoritative about the *shape* of a valid ticket and useless
about volume, at 3 tickets. `scale_helpdesk.py` multiplies it to 503 tickets /
3004 followups / 3002 attachments. That split — discover the shape, then
synthesize the volume — is the S-0.3 finding applied.

Then the two experiments:

```bash
# 3 conditions x 20 interleaved reps; the ablation delta and the stub comparison
docker compose exec $E workbench bash -c 'cd /repo/django-helpdesk && python /harness/measure.py'

# how small a delta this method can actually detect, plus its false-positive rate
docker compose exec $E workbench bash -c 'cd /repo/django-helpdesk && python /harness/calibrate.py'
```

Both write JSON to `results/`, which is gitignored — the numbers that matter are
transcribed into `FINDINGS.md`, where they can be read without a rerun.

**On Windows Git Bash**, prefix `docker compose exec` with `MSYS_NO_PATHCONV=1`,
or `PYTHONPATH=/seeds` arrives inside the container as `C:/Program Files/Git/seeds`.

---

## What is where

| File | Purpose |
|---|---|
| `seeds/spike_settings.py` | Postgres overlay. `demodesk` hardcodes SQLite with no override, so its settings are imported and overridden from outside rather than the checkout being edited |
| `seeds/scale_helpdesk.py` | Multiplies the demo object graph. Deterministic and idempotent — re-running must produce an identical dataset or measurements are not comparable across runs |
| `harness/stats.py` | Mann-Whitney U, Cliff's delta, CV. Standard library only |
| `harness/measure.py` | The ablation experiment: baseline vs replay stub vs empty stub |
| `harness/calibrate.py` | The sensitivity experiment: known injected delays, plus a false-positive rate |

The harness lives outside the checkout, like S-0.3's seeds, because the subject
repository must stay byte-identical to what was cloned.

---

## Three things that will bite anyone extending this

**`time.sleep` has a floor of ~80–100 µs per call regardless of duration.**
`time.sleep(0)` costs 7.5 ms per 100 calls. Any injection finer than that
measures the injector. This is why the calibration bounds the floor from above
and the false-positive section bounds it from below — neither alone is enough.

**Statistical separability is not sufficient to justify a finding.** Two rows of
the calibration sweep passed `p < 0.01` on shifts the injection could not have
caused. Query count and payload size were unchanged in both, which is exactly
what the guard-counter invariant exists to catch.

**A replay stub must be size-representative.** The first version recorded the
first ticket with any followups — a demo row with one, where the page median is
six. That collapses the replay stub into the empty stub and makes the two
strategies look interchangeable, which is the conclusion the experiment is
supposed to be testing rather than assuming.

---

## Scope

Spike scaffolding, not product code. Not sandboxed, no resource limits, exempt
from the production guard S-2.5 will add. The measurement approach is the
deliverable; E1 builds the real primitive, informed by ADR 008 and by the
requirements Result 2 hands to S-3.4.
