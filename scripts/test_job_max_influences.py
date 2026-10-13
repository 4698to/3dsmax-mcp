from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from maxmcp.jobs.job_model import Job
from maxmcp.jobs.scheduler import JobManager, _Worker


class JobMaxInfluencesTests(unittest.TestCase):
    def _manager(self, **kwargs) -> JobManager:
        return JobManager(
            store=Mock(),
            max_influences=kwargs.pop("server_max_influences", 8),
            max_queued_per_owner=20,
            ocr_base="",
            output_dir=".",
            **kwargs,
        )

    def test_job_serialization_and_historical_default(self) -> None:
        job = Job(job_id="job", owner="owner", max_influences=6)
        serialized = job.to_dict()
        self.assertEqual(serialized["max_influences"], 6)
        self.assertEqual(Job.from_dict(serialized).max_influences, 6)

        historical = dict(serialized)
        historical.pop("max_influences")
        self.assertIsNone(Job.from_dict(historical).max_influences)

    def test_submit_persists_max_influences(self) -> None:
        manager = self._manager()
        job = manager.submit("owner", max_influences=5)

        self.assertEqual(job.max_influences, 5)
        manager._store.create.assert_called_once_with(job)
        self.assertEqual(manager.get_dict(job.job_id)["max_influences"], 5)

    def test_submit_rejects_invalid_max_influences(self) -> None:
        manager = self._manager()
        for value in (0, -1, True, False, 1.5, "4"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    manager.submit("owner", max_influences=value)
        manager._store.create.assert_not_called()

    def test_worker_uses_job_override_or_server_default(self) -> None:
        manager = self._manager(server_max_influences=9)
        for override, expected in ((None, 9), (3, 3)):
            with self.subTest(override=override):
                job = Job(job_id=f"job-{expected}", owner="owner", max_influences=override)
                manager._jobs[job.job_id] = job
                manager._workers[job.job_id] = _Worker(
                    job_id=job.job_id, session=object(), client=object()
                )
                with patch("maxmcp.jobs.scheduler.run_goskin_job", return_value={"ok": True}) as run:
                    with patch.object(manager, "_finalize"):
                        manager._run_worker(job.job_id)
                self.assertEqual(run.call_args.kwargs["max_influences"], expected)


if __name__ == "__main__":
    unittest.main()
