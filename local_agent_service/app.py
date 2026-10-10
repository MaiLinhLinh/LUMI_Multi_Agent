"""Independent fake async Agent Service used by the Lumi integration test.

This service deliberately does not import anything from ``final_framework``.
It exposes the small HTTP async-job protocol described in
``ASYNC_TASK_END_TO_END_TEST_PLAN.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any
from uuid import uuid4

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})


@dataclass
class Job:
    job_id: str
    request_body: dict[str, Any]
    status: str = "queued"
    poll_count: int = 0


class InMemoryJobStore:
    """Thread-safe in-memory job store for the fake service."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = RLock()

    def create(self, request_body: dict[str, Any]) -> Job:
        with self._lock:
            job = Job(job_id=f"job_{uuid4().hex}", request_body=request_body)
            self._jobs[job.job_id] = job
            return job

    def poll(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status in _TERMINAL_STATES:
                return job

            job.poll_count += 1
            if job.poll_count == 1:
                job.status = "running"
            else:
                job.status = "completed"
            return job

    def cancel(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status not in _TERMINAL_STATES:
                job.status = "cancelled"
            return job


def _completed_payload(job: Job) -> dict[str, Any]:
    task = job.request_body.get("task")
    task_data = task if isinstance(task, dict) else {}
    task_id = task_data.get("task_id", "unknown")
    task_id_text = task_id if isinstance(task_id, str) else "unknown"
    confirmation_code = task_id_text[-8:].upper()
    return {
        "status": "completed",
        "payload": {
            "passage": f"ASYNC_TASK_OK: {confirmation_code}"
        },
    }


def _job_response(job: Job) -> dict[str, Any]:
    if job.status == "completed":
        return _completed_payload(job)
    return {"status": job.status}


def create_app(*, job_store: InMemoryJobStore | None = None) -> Starlette:
    store = job_store or InMemoryJobStore()

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def submit_task(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"error": "request body must be valid JSON"}, status_code=400
            )
        if not isinstance(body, dict):
            return JSONResponse(
                {"error": "request body must be a JSON object"}, status_code=400
            )

        job = store.create(body)
        return JSONResponse({"job_id": job.job_id}, status_code=202)

    async def get_job(request: Request) -> JSONResponse:
        job = store.poll(request.path_params["job_id"])
        if job is None:
            return JSONResponse({"error": "job not found"}, status_code=404)
        return JSONResponse(_job_response(job))

    async def cancel_job(request: Request) -> JSONResponse:
        job = store.cancel(request.path_params["job_id"])
        if job is None:
            return JSONResponse({"error": "job not found"}, status_code=404)
        return JSONResponse(_job_response(job))

    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/tasks", submit_task, methods=["POST"]),
            Route("/jobs/{job_id}", get_job, methods=["GET"]),
            Route("/jobs/{job_id}/cancel", cancel_job, methods=["POST"]),
        ]
    )
    app.state.job_store = store
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8010)
