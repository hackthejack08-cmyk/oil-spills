"""Tiny background-job manager (thread pool) with progress reporting.
Adequate for a single-node deployment; swap for Celery/RQ if multi-user."""
from __future__ import annotations
import gc

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

_POOL = ThreadPoolExecutor(max_workers=3)
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def submit(kind: str, fn: Callable, *args, **kwargs) -> dict:
    jid = f"job_{uuid.uuid4().hex[:10]}"
    job = {"id": jid, "kind": kind, "status": "queued", "progress": 0.0, "message": "", "result": None, "error": None,
           "created_at": datetime.now(timezone.utc).isoformat(), "finished_at": None}
    with _LOCK:
        _JOBS[jid] = job

    def progress(done, total=0, message=None):
        with _LOCK:
            if total:
                job["progress"] = round(min(done / total, 1.0), 3)
            if message:
                job["message"] = message
            elif total:
                job["message"] = f"{done/1e6:.0f} / {total/1e6:.0f} MB"

    def run():
        try:
            _run()
        finally:
            gc.collect()

    def _run():
        job["status"] = "running"
        try:
            job["result"] = fn(*args, progress=progress, **kwargs)
            job["status"] = "done"; job["progress"] = 1.0
        except Exception as exc:  # noqa
            job["status"] = "error"; job["error"] = f"{type(exc).__name__}: {exc}"; job["trace"] = traceback.format_exc()[-2000:]
        job["finished_at"] = datetime.now(timezone.utc).isoformat()

    _POOL.submit(run)
    return job


def get(jid: str) -> dict | None:
    return _JOBS.get(jid)


def all_jobs() -> list[dict]:
    return sorted(_JOBS.values(), key=lambda j: j["created_at"], reverse=True)[:50]
