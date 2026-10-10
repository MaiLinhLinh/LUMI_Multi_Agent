from __future__ import annotations

import unittest

from starlette.testclient import TestClient

from local_agent_service.app import create_app


class AsyncAgentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app())

    @staticmethod
    def _request_body() -> dict[str, object]:
        return {
            "task": {
                "run_id": "run_test",
                "task_id": "task_test",
                "task_type": "remote_text_task",
                "evidence_ref": "#E1",
                "instruction": "Trả mã xác nhận.",
            },
            "input": {"query": "Trả mã xác nhận."},
            "framework_context": {
                "knowledge_catalog": [],
                "tool_grants": [],
            },
        }

    def test_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_submit_and_poll_until_completed(self) -> None:
        submitted = self.client.post("/tasks", json=self._request_body())
        self.assertEqual(submitted.status_code, 202)
        job_id = submitted.json()["job_id"]
        self.assertIsInstance(job_id, str)
        self.assertTrue(job_id)

        running = self.client.get(f"/jobs/{job_id}")
        self.assertEqual(running.status_code, 200)
        self.assertEqual(running.json(), {"status": "running"})

        completed = self.client.get(f"/jobs/{job_id}")
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "completed")
        self.assertIn("ASYNC_TASK_OK", completed.json()["payload"]["passage"])

    def test_cancel(self) -> None:
        submitted = self.client.post("/tasks", json=self._request_body())
        job_id = submitted.json()["job_id"]

        cancelled = self.client.post(f"/jobs/{job_id}/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json(), {"status": "cancelled"})

        polled = self.client.get(f"/jobs/{job_id}")
        self.assertEqual(polled.json(), {"status": "cancelled"})

    def test_unknown_job_returns_404(self) -> None:
        response = self.client.get("/jobs/missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "job not found"})


if __name__ == "__main__":
    unittest.main()
