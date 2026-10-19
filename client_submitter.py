"""ele_sim Client adapter used by the LLM optimizer.

This file is intentionally self-contained so the project can be moved without
importing anything from the PSO project.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any
from uuid import UUID

LOGGER = logging.getLogger(__name__)
LOCAL_TO_REMOTE = {"a":"a", "b":"b", "l1":"L1", "l2":"L2", "l3":"L3", "s1":"S1", "s2":"S2", "s3":"S3", "f_l":"f_l"}

def sample_to_remote_payload(sample: dict[str, float]) -> dict[str, float]:
    if set(sample) != set(LOCAL_TO_REMOTE):
        raise ValueError(f"candidate variables must be exactly {sorted(LOCAL_TO_REMOTE)}")
    payload = {remote: float(sample[local]) for local, remote in LOCAL_TO_REMOTE.items()}
    if not all(math.isfinite(value) for value in payload.values()):
        raise ValueError("candidate contains non-finite values")
    return payload

def _status(value: Any) -> str:
    return str(getattr(value, "value", value)).upper()

def report_progress(progress: Any) -> None:
    """Log every state count exposed by ele_sim's BatchProgress object."""
    LOGGER.info(
        "远程批次进度：batch_id=%s, done=%d/%d, success=%d, running=%d, "
        "pending=%d, retry=%d, failed=%d, canceled=%d, not_found=%d, "
        "waiting=%d, elapsed_seconds=%.1f",
        progress.batch_id,
        progress.completed,
        progress.total,
        progress.success,
        progress.running,
        progress.pending,
        progress.retry_waiting,
        progress.failed,
        progress.canceled,
        progress.not_found,
        progress.total - progress.completed,
        progress.elapsed_seconds,
    )

def normalize_job_result(job_result: Any) -> dict[str, Any]:
    if _status(job_result.status) != "SUCCESS":
        raise RuntimeError(f"remote task failed: {getattr(job_result, 'error', '')}")
    payload = job_result.result
    frequency = [float(v) for v in payload["frequency_data"]]
    s11 = [float(v) for v in payload["s11_data"]]
    if len(frequency) < 2 or len(frequency) != len(s11) or not all(math.isfinite(v) for v in frequency + s11):
        raise ValueError("remote result frequency_data/s11_data is invalid")
    return {"Frequency_GHz": frequency, "S11_dB": s11, "status": "remote_success", "remote_task_id": str(job_result.task_id), "remote_attempt": int(job_result.attempt), "remote_worker_id": job_result.worker_id, "remote_plugin_version": job_result.plugin_version, "remote_runtime_seconds": job_result.runtime_seconds}

def run_simulation_batch(database_url: str, candidates: list[dict[str, float]], *, simulation_type: str, priority: int = 0, max_retries: int = 3, task_timeout_seconds: int = 7200, wait_timeout_seconds: float = 0, poll_interval_seconds: float = 2, progress_interval_seconds: float = 45, batch_id: str | None = None, on_submitted: Callable[[list[str]], None] | None = None) -> tuple[list[str], dict[str, Any]]:
    if not candidates:
        return [], {}
    from ele_sim_client import SimulationClient
    from ele_sim_core.db import JobRepository
    client = SimulationClient(simulation_type, JobRepository(database_url))
    payloads = [sample_to_remote_payload(candidate) for candidate in candidates]
    submitted: list[UUID] = []
    try:
        submitted = list(client.submit_batch(payloads, priority=priority, max_retries=max_retries, timeout_seconds=task_timeout_seconds))
        task_ids = [str(task_id) for task_id in submitted]
        if len(task_ids) != len(candidates):
            raise RuntimeError("database returned a task count different from candidates")
        if on_submitted:
            on_submitted(task_ids)
        results = client.wait_batch(submitted, timeout=None if wait_timeout_seconds <= 0 else wait_timeout_seconds, poll_interval=poll_interval_seconds, progress_interval=progress_interval_seconds, batch_id=batch_id, on_progress=report_progress)
        return task_ids, {str(task_id): value for task_id, value in results.items()}
    except Exception:
        if submitted:
            try:
                client.cancel_batch(submitted)
            except Exception:
                LOGGER.exception("failed to cancel remote tasks after batch error")
        raise

def wait_simulation_batch(database_url: str, task_ids: list[str], *, simulation_type: str, wait_timeout_seconds: float = 0, poll_interval_seconds: float = 2, progress_interval_seconds: float = 45, batch_id: str | None = None) -> dict[str, Any]:
    if not task_ids:
        return {}
    from ele_sim_client import SimulationClient
    from ele_sim_core.db import JobRepository
    client = SimulationClient(simulation_type, JobRepository(database_url))
    submitted = [UUID(task_id) for task_id in task_ids]
    try:
        results = client.wait_batch(submitted, timeout=None if wait_timeout_seconds <= 0 else wait_timeout_seconds, poll_interval=poll_interval_seconds, progress_interval=progress_interval_seconds, batch_id=batch_id, on_progress=report_progress)
        return {str(task_id): value for task_id, value in results.items()}
    except Exception:
        try:
            client.cancel_batch(submitted)
        except Exception:
            LOGGER.exception("failed to cancel resumed remote tasks after wait error")
        raise
