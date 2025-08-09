import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "src.settings")

app = Celery("src")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(name="healthcheck.ping")
def ping():
    """
    Lightweight task that returns a static response for worker health checks.
    """
    return "pong"
