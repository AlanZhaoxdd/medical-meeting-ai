from celery import Celery

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery(
    "medical_kb",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.worker.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "reconcile-vector-outbox": {
            "task": "app.worker.tasks.sync_outbox",
            "schedule": 30.0,
        },
        "reconcile-stale-meeting-imports": {
            "task": "app.worker.tasks.reconcile_meeting_imports",
            "schedule": 60.0,
        },
        "reconcile-queued-ingestion-jobs": {
            "task": "app.worker.tasks.reconcile_ingestion_jobs",
            "schedule": 30.0,
        },
        "reconcile-stale-exports": {
            "task": "app.worker.tasks.reconcile_export_jobs",
            "schedule": 60.0,
        },
    },
)
