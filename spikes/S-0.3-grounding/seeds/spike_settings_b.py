# Postgres overlay for candidate B (django-helpdesk).
#
# `demodesk/config/settings.py:140-145` hardcodes the SQLite engine and path.
# There is no `DATABASE_URL`, no `DB_*` environment variable, and no
# `local_settings` import hook anywhere in that file — the only two `os.getenv`
# calls in it are for the teams feature flag. So there is no supported way to
# point the demo project at Postgres.
#
# The spike's rule is that a repository which hardcodes its database settings is
# an obstacle to record, not a file to patch. This module is the way to honour
# that: it imports the project's own settings and overrides `DATABASES`
# afterwards, so the checkout stays byte-identical to what was cloned. It lives
# in /seeds (mounted read-only), reached via PYTHONPATH, and is selected with
# DJANGO_SETTINGS_MODULE=spike_settings_b.
#
# Worth being clear about what this does and does not prove: an overlay works
# here only because the settings are a plain module doing no I/O at import. It
# is not a general answer, and the Explorer meeting the same wall would need
# this trick as a *tool*, not as an improvisation.
from __future__ import annotations

import os

from demodesk.config.settings import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.getenv("DB_HOST", "postgres"),
        "PORT": os.getenv("DB_PORT", "5432"),
        "NAME": os.getenv("DB_NAME", "spike_b"),
        "USER": os.getenv("DB_USER", "coldfix_test"),
        "PASSWORD": os.getenv("DB_PASSWORD", "coldfix_test"),
    }
}
