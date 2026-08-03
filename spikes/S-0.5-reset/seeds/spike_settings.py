# Postgres overlay for the reset subject.
#
# Same manoeuvre as S-0.3 obstacle B-1 and S-0.4: demodesk hardcodes SQLite with
# no environment override and no local_settings hook, so its settings are
# imported and overridden from outside rather than the checkout being edited.
from __future__ import annotations

import os

from demodesk.config.settings import *  # noqa: F403

DEBUG = False
ALLOWED_HOSTS = ["*", "testserver"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.getenv("DB_HOST", "postgres"),
        "PORT": os.getenv("DB_PORT", "5432"),
        "NAME": os.getenv("DB_NAME", "spike_reset"),
        "USER": os.getenv("DB_USER", "coldfix_test"),
        "PASSWORD": os.getenv("DB_PASSWORD", "coldfix_test"),
        # Zero, deliberately, and this one matters for what is being measured.
        #
        # A persistent connection is exactly where the state this spike hunts
        # for hides: server-side prepared statements, session GUCs, and an open
        # snapshot all outlive a ROLLBACK on the same connection. Holding a
        # connection open across cycles would make the rollback strategy look
        # cleaner or dirtier than it is depending on settings unrelated to
        # reset. Starting from a fresh connection each cycle isolates the
        # question to "did the rollback restore the database", and the
        # connection-level checks are made explicitly instead.
        "CONN_MAX_AGE": 0,
    }
}
