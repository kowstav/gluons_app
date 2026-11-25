"""Offline guardrail tests using recorded traffic-shaped Cloud Run responses."""
from __future__ import annotations

import unittest
from unittest.mock import Mock

import cloud_run_rollout as rollout

IMAGES = {"api": "registry/api:release", "web": "registry/web:release"}


class CloudRun:
    def __init__(self) -> None:
        self.previous = {"old-a": 60, "old-b": 40}
        self.splits = {f"gluons-{role}": self.previous.copy() for role in rollout.SERVICES}
        self.revisions: dict[str, str] = {}
        self.traffic_writes: list[tuple[str, str]] = []
        self.fail_shift: str | None = None
        self.bad_revision: str | None = None

    def __call__(self, resource: str, action: str, name: str, *args: str) -> dict:
        if resource == "revisions":
            assert action == "describe" and name in self.revisions.values()
            ready = "False" if self.revisions.get(self.bad_revision or "") == name else "True"
            return {"metadata": {"name": name}, "status": {"conditions": [
                {"type": "Ready", "status": ready, "reason": "Retired"},
                {"type": "ContainerReady", "status": "True"},
            ]}}
        assert resource == "services"
        if action == "update":
            assert "--no-traffic" in args
            assert args[args.index("--image") + 1] == IMAGES["web" if name == "gluons-web" else "api"]
            suffix = args[args.index("--revision-suffix") + 1]
            self.revisions[name] = f"{name}-{suffix}"
            return {}
        if action == "update-traffic":
            split = args[1]
            self.traffic_writes.append((name, split))
            desired = {revision: int(percent) for revision, percent in (entry.split("=") for entry in split.split(","))}
            if sum(desired.values()) == 10:
                desired.update({"old-a": 54, "old-b": 36})
            self.splits[name] = desired
            if name == self.fail_shift and self.revisions.get(name) in desired:
                raise RuntimeError("response lost after traffic update")
            return {}
        assert action == "describe"
        revision = self.revisions.get(name)
        return {"status": {"traffic": [{"revisionName": rev, "percent": share} for rev, share in self.splits[name].items()],
                           "latestCreatedRevisionName": revision,
                           "latestReadyRevisionName": "old-a",
                           "conditions": [{"type": "Ready", "status": "True"}]}}


class RolloutTest(unittest.TestCase):
    def test_ready_zero_traffic_revision_can_become_the_ten_percent_canary(self) -> None:
        cloud = CloudRun()
        smoke = Mock()
        candidates = rollout.roll_out(cloud, "gluons", "r123-1", IMAGES, 10, smoke)
        smoke.assert_called_once_with()
        self.assertEqual(len(cloud.traffic_writes), 5)
        for name, revision in candidates.items():
            self.assertEqual(cloud.splits[name], {"old-a": 54, "old-b": 36, revision: 10})

    def test_staging_smoke_failure_restores_every_previous_split(self) -> None:
        cloud = CloudRun()
        smoke = Mock(side_effect=RuntimeError("smoke failed"))
        with self.assertRaisesRegex(RuntimeError, "smoke failed"):
            rollout.roll_out(cloud, "gluons", "r123-1", IMAGES, 100, smoke)
        self.assertEqual(len(cloud.traffic_writes), 10)
        self.assertTrue(all(split == cloud.previous for split in cloud.splits.values()))

    def test_lost_update_response_restores_the_attempted_service_as_well(self) -> None:
        cloud = CloudRun()
        cloud.fail_shift = "gluons-webhooks"
        smoke = Mock()
        with self.assertRaisesRegex(RuntimeError, "response lost"):
            rollout.roll_out(cloud, "gluons", "r123-1", IMAGES, 10, smoke)
        smoke.assert_not_called()
        self.assertEqual(cloud.traffic_writes[-2:], [
            ("gluons-webhooks", "old-a=60,old-b=40"), ("gluons-worker", "old-a=60,old-b=40"),
        ])
        self.assertTrue(all(split == cloud.previous for split in cloud.splits.values()))

    def test_ready_service_cannot_pass_when_the_exact_revision_is_not_ready(self) -> None:
        cloud = CloudRun()
        cloud.bad_revision = "gluons-web"
        with self.assertRaisesRegex(RuntimeError, "new, ready candidate"):
            rollout.roll_out(cloud, "gluons", "r123-1", IMAGES, 10, Mock())
        self.assertEqual(cloud.traffic_writes, [])

    def test_unresolved_traffic_cannot_proceed(self) -> None:
        with self.assertRaisesRegex(ValueError, "resolved current traffic split"):
            rollout.traffic_split({"status": {"traffic": [{"latestRevision": True, "percent": 100}]}})


if __name__ == "__main__":
    unittest.main()
