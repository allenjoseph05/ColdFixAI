# Multiply the demo object graph up to a size where the N+1 is measurable.
#
# This is the S-0.3 finding applied: discovery and synthesis compose rather than
# compete. `demodesk/fixtures/demo.json` is authoritative about the *shape* of a
# valid ticket — which fields are required, how followups and attachments hang
# off it — and useless about volume, at 3 tickets. So the fixture is loaded
# first and this script multiplies it.
#
# Deliberately uniform: every ticket gets exactly FOLLOWUPS_PER_TICKET followups
# and every followup exactly one attachment. Skewed distributions are S-3.3's
# subject, and the note on that story is right that uniform data hides
# skew-dependent defects. But this spike measures *variance in the measurement
# itself*, and a skewed dataset would make per-request work depend on which
# tickets a page happened to contain — adding a source of spread that has
# nothing to do with the instrument under test. Uniformity is the control.
#
# Fully deterministic: no RNG, no timestamps read from the clock for content.
# Re-running it must produce a byte-identical dataset or measurements taken
# across runs are not comparable.
#
# Run:
#   python manage.py shell < /seeds/scale_helpdesk.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from helpdesk.models import FollowUp, FollowUpAttachment, Queue, Ticket

N_TICKETS = 500
FOLLOWUPS_PER_TICKET = 6
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

queue = Queue.objects.order_by("id").first()
if queue is None:
    raise SystemExit("no Queue — load demodesk/fixtures/demo.json first")

# Idempotent: drop anything a previous run of this script created, leaving the
# demo fixture's own rows alone.
FollowUpAttachment.objects.filter(followup__title__startswith="scaled-").delete()
FollowUp.objects.filter(title__startswith="scaled-").delete()
Ticket.objects.filter(title__startswith="scaled-").delete()

tickets = [
    Ticket(
        title=f"scaled-ticket-{i:04d}",
        queue=queue,
        created=EPOCH + timedelta(minutes=i),
        modified=EPOCH + timedelta(minutes=i),
        submitter_email=f"user{i:04d}@example.org",
        status=Ticket.OPEN_STATUS,
        priority=(i % 5) + 1,
        description=(
            f"Synthesized by the S-0.4 ablation spike, ticket {i}. "
            "Body text is padded so serialization does a realistic amount of "
            "work per row rather than copying a short string. " * 3
        ),
    )
    for i in range(N_TICKETS)
]
Ticket.objects.bulk_create(tickets, batch_size=500)
created = list(Ticket.objects.filter(title__startswith="scaled-").order_by("id"))

followups = [
    FollowUp(
        ticket=ticket,
        title=f"scaled-followup-{t_index:04d}-{f_index}",
        date=EPOCH + timedelta(minutes=t_index, seconds=f_index * 30),
        public=True,
        comment=(
            f"Follow-up {f_index} on ticket {t_index}. "
            "Padded for the same reason as the ticket description. " * 3
        ),
    )
    for t_index, ticket in enumerate(created)
    for f_index in range(FOLLOWUPS_PER_TICKET)
]
FollowUp.objects.bulk_create(followups, batch_size=1000)
made = list(FollowUp.objects.filter(title__startswith="scaled-").order_by("id"))

# One attachment per followup. `file` is a plain name — DRF's FileField
# serializes it by asking storage for a URL, which is string construction and
# does not touch the filesystem. Writing real files would add disk I/O to every
# measured request, which is variance this spike would then have to explain.
FollowUpAttachment.objects.bulk_create(
    [
        FollowUpAttachment(
            followup=followup,
            file=f"helpdesk/attachments/scaled/{followup.id}/evidence.txt",
            filename="evidence.txt",
            mime_type="text/plain",
            size=2048,
        )
        for followup in made
    ],
    batch_size=1000,
)

print(
    f"SCALED tickets={Ticket.objects.count()} "
    f"followups={FollowUp.objects.count()} "
    f"attachments={FollowUpAttachment.objects.count()}"
)
