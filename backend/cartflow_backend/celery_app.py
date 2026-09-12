import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cartflow_backend.settings")

app = Celery("cartflow_backend")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks(["flows"])
