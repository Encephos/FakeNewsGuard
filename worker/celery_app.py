"""Celery application instance for FakeNewsGuard workers.

Start a worker with:
    celery -A worker.celery_app worker --loglevel=info --concurrency=4
"""

from __future__ import annotations

import time

from celery import Celery
from celery.signals import task_prerun, task_postrun, task_retry

from config.infrastructure import CeleryConfig
from tools.telemetry import CELERY_TASKS_TOTAL, CELERY_TASK_DURATION, CELERY_ACTIVE_TASKS

_task_start_times: dict[str, float] = {}

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

# Explicitly import eval_tasks so the evaluation task is registered
import worker.eval_tasks  # noqa: F401


@task_prerun.connect
def on_task_prerun(task_id: str, task, **kwargs) -> None:
    _task_start_times[task_id] = time.monotonic()
    CELERY_ACTIVE_TASKS.inc()


@task_postrun.connect
def on_task_postrun(task_id: str, task, state: str, **kwargs) -> None:
    from celery import states as celery_states
    start = _task_start_times.pop(task_id, None)
    if start is not None:
        CELERY_TASK_DURATION.labels(task_name=task.name).observe(
            time.monotonic() - start
        )
    CELERY_ACTIVE_TASKS.dec()
    outcome = "success" if state == celery_states.SUCCESS else "failure"
    CELERY_TASKS_TOTAL.labels(task_name=task.name, state=outcome).inc()


@task_retry.connect
def on_task_retry(sender, **kwargs) -> None:
    CELERY_TASKS_TOTAL.labels(task_name=sender.name, state="retry").inc()
