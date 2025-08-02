#!/usr/bin/env bash
set -euo pipefail

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-src.settings}"

wait_for_db() {
  python <<'PY'
import os
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", os.getenv("DJANGO_SETTINGS_MODULE", "src.settings"))

import django
from django.db import connections
from django.db.utils import OperationalError

django.setup()

for attempt in range(1, 31):
    try:
        connections["default"].cursor()
    except OperationalError:
        time.sleep(1)
    else:
        break
PY
}

wait_for_db

if [[ "${SKIP_MIGRATE:-0}" != "1" ]]; then
  python manage.py migrate --noinput
fi

if [[ "${SKIP_COLLECTSTATIC:-0}" != "1" ]]; then
  python manage.py collectstatic --noinput
fi

exec "$@"
