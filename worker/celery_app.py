"""Celery application instance for FakeNewsGuard workers.

Start a worker with:
    celery -A worker.celery_app worker --loglevel=info --concurrency=4
"""

from __future__ import annotations

from celery import Celery

from config.infrastructure import CeleryConfig

cfg = CeleryConfig()

celery_app = Celery("fakenewsguard")
celery_app.conf.update(
    broker_url=cfg.broker_url,
    result_backend=cfg.result_backend,
    task_time_limit=cfg.task_time_limit,
    task_soft_time_limit=cfg.task_soft_time_limit,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    worker_hijack_root_logger=False,
    # Celery Beat schedule for stale-job watchdog
    beat_schedule={
        "check-stale-jobs": {
            "task": "fakenewsguard.check_stale_jobs",
            "schedule": 30.0,
        },
    },
)

# Auto-discover tasks in worker.tasks
celery_app.autodiscover_tasks(["worker"])
