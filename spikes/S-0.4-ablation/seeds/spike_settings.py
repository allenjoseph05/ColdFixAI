# Postgres overlay for the ablation subject.
#
# Same manoeuvre as S-0.3 obstacle B-1: demodesk hardcodes SQLite at
# demodesk/config/settings.py:140-145 with no environment override and no
# local_settings hook, so the only way to point it at Postgres without editing
# the checkout is to import its settings and override them from outside.
#
# DEBUG stays False. Django's DEBUG cursor records every statement's SQL text,
# which is itself measurable work — and this spike measures response time, so
# instrumenting the thing under test would be self-defeating. Query counting in
# the harness uses force_debug_cursor per ADR 008, enabled only for the counting
# pass and never during a timed run.
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
        "NAME": os.getenv("DB_NAME", "spike_ablation"),
        "USER": os.getenv("DB_USER", "coldfix_test"),
        "PASSWORD": os.getenv("DB_PASSWORD", "coldfix_test"),
        # Reconnecting mid-run would add a connection setup cost to whichever
        # condition happened to be running when it expired, which interleaving
        # cannot cancel because it is not periodic.
        "CONN_MAX_AGE": 600,
    }
}

# Whitenoise/compressor style middleware and template caching are irrelevant to a
# JSON endpoint but add variance; nothing here changes them, and that is
# deliberate — the subject is measured as it ships.
