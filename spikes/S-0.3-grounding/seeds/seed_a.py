# Seed for candidate A (healthchecks).
#
# Lives outside the checkout on purpose. The spike's recording discipline says
# not to modify the repository under test, and a seed script committed into the
# tree is a modification — it would also make the next run of this spike easier
# than the first, which is the measurement being taken.
#
# The object graph below is not obvious from the models. It was recovered from
# `hc/test.py::BaseTestCase`, which is the only place in the repository that
# spells out that a usable account is User + Project + Profile, that the API key
# is a plain column on Project, and that a Project without a Profile on its
# owner will authenticate and then fail. That is the finding: this repo's
# "fixtures" are a test base class, not anything `loaddata` can read.
#
# Run:
#   python manage.py shell < /repos/../seeds/seed_a.py
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.models import User
from django.utils.timezone import now

from hc.accounts.models import Profile, Project
from hc.api.models import Check, Ping

API_KEY = "X" * 32
N_CHECKS = 50
PINGS_PER_CHECK = 20

alice, _ = User.objects.get_or_create(
    username="alice", defaults={"email": "alice@example.org"}
)
alice.set_password("password")
alice.save()

Profile.objects.get_or_create(user=alice)

project, _ = Project.objects.get_or_create(
    owner=alice,
    name="Alices Project",
    defaults={"api_key": API_KEY, "badge_key": alice.username},
)
if project.api_key != API_KEY:
    project.api_key = API_KEY
    project.save()

Ping.objects.filter(owner__project=project).delete()
Check.objects.filter(project=project).delete()

start = now() - timedelta(days=1)
for i in range(N_CHECKS):
    check = Check.objects.create(
        project=project,
        name=f"seeded-check-{i:03d}",
        slug=f"seeded-check-{i:03d}",
        tags="spike seeded",
        desc=f"Synthesized by the S-0.3 grounding spike, check {i}.",
        n_pings=PINGS_PER_CHECK,
        last_ping=start + timedelta(minutes=PINGS_PER_CHECK),
    )
    Ping.objects.bulk_create(
        [
            Ping(
                owner=check,
                n=n + 1,
                created=start + timedelta(minutes=n),
                kind="" if n % 5 else "fail",
                scheme="https",
                remote_addr="203.0.113.7",
                method="GET",
                ua="coldfix-spike/0.3",
            )
            for n in range(PINGS_PER_CHECK)
        ]
    )

print(
    f"SEEDED users={User.objects.count()} projects={Project.objects.count()} "
    f"checks={Check.objects.count()} pings={Ping.objects.count()} api_key={API_KEY}"
)
