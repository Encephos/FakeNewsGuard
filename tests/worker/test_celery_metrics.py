"""Tests for Celery signal handler functions in worker/celery_app.py."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_metrics(mocker):
    """Patch all Celery telemetry metrics used by signal handlers."""
    return {
        "tasks_total": mocker.patch("worker.celery_app.CELERY_TASKS_TOTAL"),
        "task_duration": mocker.patch("worker.celery_app.CELERY_TASK_DURATION"),
        "active_tasks": mocker.patch("worker.celery_app.CELERY_ACTIVE_TASKS"),
    }


@pytest.fixture(autouse=True)
def clear_start_times():
    """Ensure _task_start_times is empty before and after each test."""
    from worker.celery_app import _task_start_times
    _task_start_times.clear()
    yield
    _task_start_times.clear()


# ── task_prerun ───────────────────────────────────────────────────────────────

def test_prerun_records_start_time(mock_metrics):
    from worker.celery_app import on_task_prerun, _task_start_times

    task = MagicMock()
    task.name = "fakenewsguard.analyze"

    before = time.monotonic()
    on_task_prerun(task_id="task-1", task=task)
    after = time.monotonic()

    assert "task-1" in _task_start_times
    assert before <= _task_start_times["task-1"] <= after


def test_prerun_increments_active_tasks(mock_metrics):
    from worker.celery_app import on_task_prerun

    task = MagicMock()
    task.name = "fakenewsguard.analyze"

    on_task_prerun(task_id="task-1", task=task)

    mock_metrics["active_tasks"].inc.assert_called_once()


# ── task_postrun ──────────────────────────────────────────────────────────────

def test_postrun_decrements_active_tasks(mock_metrics):
    from worker.celery_app import on_task_prerun, on_task_postrun

    task = MagicMock()
    task.name = "fakenewsguard.analyze"

    on_task_prerun(task_id="task-2", task=task)
    on_task_postrun(task_id="task-2", task=task, state="SUCCESS")

    mock_metrics["active_tasks"].dec.assert_called_once()


def test_postrun_observes_duration(mock_metrics):
    from worker.celery_app import on_task_postrun, _task_start_times

    task = MagicMock()
    task.name = "fakenewsguard.analyze"
    _task_start_times["task-3"] = time.monotonic() - 5.0

    on_task_postrun(task_id="task-3", task=task, state="SUCCESS")

    labels_mock = mock_metrics["task_duration"].labels.return_value
    labels_mock.observe.assert_called_once()
    observed_duration = labels_mock.observe.call_args[0][0]
    assert observed_duration >= 5.0


def test_postrun_records_success_state(mock_metrics):
    from worker.celery_app import on_task_postrun

    task = MagicMock()
    task.name = "fakenewsguard.analyze"

    on_task_postrun(task_id="task-4", task=task, state="SUCCESS")

    mock_metrics["tasks_total"].labels.assert_called_with(
        task_name="fakenewsguard.analyze", state="success"
    )


def test_postrun_records_failure_state(mock_metrics):
    from worker.celery_app import on_task_postrun

    task = MagicMock()
    task.name = "fakenewsguard.analyze"

    on_task_postrun(task_id="task-5", task=task, state="FAILURE")

    mock_metrics["tasks_total"].labels.assert_called_with(
        task_name="fakenewsguard.analyze", state="failure"
    )


def test_postrun_cleans_up_start_time(mock_metrics):
    from worker.celery_app import on_task_prerun, on_task_postrun, _task_start_times

    task = MagicMock()
    task.name = "fakenewsguard.analyze"

    on_task_prerun(task_id="task-6", task=task)
    assert "task-6" in _task_start_times

    on_task_postrun(task_id="task-6", task=task, state="SUCCESS")
    assert "task-6" not in _task_start_times


def test_postrun_without_prerun_does_not_crash(mock_metrics):
    """Postrun fired without matching prerun (e.g. after worker restart)."""
    from worker.celery_app import on_task_postrun

    task = MagicMock()
    task.name = "fakenewsguard.analyze"

    on_task_postrun(task_id="unknown-task", task=task, state="FAILURE")

    mock_metrics["active_tasks"].dec.assert_called_once()
    mock_metrics["tasks_total"].labels.assert_called_once()


# ── task_retry ────────────────────────────────────────────────────────────────

def test_retry_records_retry_state(mock_metrics):
    from worker.celery_app import on_task_retry

    sender = MagicMock()
    sender.name = "fakenewsguard.analyze"

    on_task_retry(sender=sender)

    mock_metrics["tasks_total"].labels.assert_called_with(
        task_name="fakenewsguard.analyze", state="retry"
    )
    mock_metrics["tasks_total"].labels.return_value.inc.assert_called_once()
