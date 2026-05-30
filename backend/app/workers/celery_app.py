"""
PSX Sentinel — Celery Application Configuration

Defines the Celery application instance with:
- Redis as both broker and result backend
- JSON serialization for all messages
- Pakistan timezone (Asia/Karachi) for schedule alignment
- Task routing to dedicated queues (analysis vs pipeline)
- Beat schedule for the nightly data pipeline
- Late ack + prefetch=1 for reliable task execution

Workers are started with:
  celery -A app.workers.celery_app worker -Q analysis,pipeline -l INFO
  celery -A app.workers.celery_app beat -l INFO
"""

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "psx_sentinel",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    # ── Serialization ─────────────────────────────────────────────────────
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # ── Timezone ──────────────────────────────────────────────────────────
    timezone="Asia/Karachi",
    enable_utc=True,

    # ── Reliability ───────────────────────────────────────────────────────
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,

    # ── Task Routing ──────────────────────────────────────────────────────
    task_routes={
        "app.workers.tasks.run_analysis": {"queue": "analysis"},
        "app.workers.tasks.run_nightly_pipeline": {"queue": "pipeline"},
    },

    # ── Beat Schedule ─────────────────────────────────────────────────────
    beat_schedule={
        "nightly-pipeline": {
            "task": "app.workers.tasks.run_nightly_pipeline",
            "schedule": crontab(
                hour=settings.NIGHTLY_PIPELINE_HOUR,
                minute=0,
            ),
        },
    },
)
