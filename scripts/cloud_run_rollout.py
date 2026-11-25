"""Shift traffic to exact, ready Cloud Run revisions after images and migrations are prepared."""
from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from typing import Any

Gcloud = Callable[..., dict[str, Any]]
SERVICES = ("worker", "webhooks", "mcp", "api", "web")


def traffic_split(description: dict[str, Any]) -> dict[str, int]:
    """Resolve any LATEST alias to the revision actually serving before the rollout."""
    split: dict[str, int] = {}
    for entry in description.get("status", {}).get("traffic", []):
        percent = entry.get("percent", 0)
        if percent == 0:
            continue
        revision = entry.get("revisionName")
        if not revision or type(percent) is not int or not 0 < percent <= 100:
            raise ValueError("Cloud Run did not report a resolved current traffic split.")
        split[revision] = split.get(revision, 0) + percent
    if sum(split.values()) != 100:
        raise ValueError("Cloud Run did not report a complete current traffic split.")
    return split


def check_candidate(gcloud: Gcloud, name: str, revision: str, previous: dict[str, int]) -> None:
    description = gcloud("services", "describe", name)
    status = description.get("status", {})
    service_ready = any(item.get("type") == "Ready" and item.get("status") == "True"
                        for item in status.get("conditions", []))
    if not service_ready or revision != status.get("latestCreatedRevisionName") or revision in previous:
        raise RuntimeError("Cloud Run did not report a new, ready candidate revision.")
    if traffic_split(description) != previous:
        raise RuntimeError("Serving traffic changed during candidate preparation.")
    # A zero-traffic revision can be Ready with reason Retired while the service's
    # latestReadyRevisionName still names the revision serving requests.
    candidate = gcloud("revisions", "describe", revision)
    conditions = {item.get("type"): item.get("status") for item in candidate.get("status", {}).get("conditions", [])}
    if (candidate.get("metadata", {}).get("name") != revision
            or conditions.get("Ready") != "True" or conditions.get("ContainerReady") != "True"):
        raise RuntimeError("Cloud Run did not report a new, ready candidate revision.")


def roll_out(gcloud: Gcloud, prefix: str, suffix: str, images: dict[str, str], percent: int,
             smoke: Callable[[], None]) -> dict[str, str]:
    if percent not in {10, 100}:
        raise ValueError("Use the staging rollout or the bounded production canary.")
    names = [f"{prefix}-{role}" for role in SERVICES]
    candidates = {name: f"{name}-{suffix}" for name in names}
    if any(len(revision) > 63 for revision in candidates.values()):
        raise ValueError("A candidate revision name exceeds the Cloud Run length limit.")
    previous = {name: traffic_split(gcloud("services", "describe", name)) for name in names}
    for role, name in zip(SERVICES, names, strict=True):
        gcloud("services", "update", name, "--image", images["web" if role == "web" else "api"],
               "--revision-suffix", suffix, "--no-traffic")
        check_candidate(gcloud, name, candidates[name], previous[name])

    print(f"Previous traffic splits: {json.dumps(previous, sort_keys=True)}", flush=True)
    attempted = []
    try:
        for name, revision in candidates.items():
            check_candidate(gcloud, name, revision, previous[name])
            # Record the attempt first. A request may apply before the CLI sees its response.
            attempted.append(name)
            gcloud("services", "update-traffic", name, "--to-revisions", f"{revision}={percent}")
            current = traffic_split(gcloud("services", "describe", name))
            if current.get(revision) != percent:
                raise RuntimeError(f"Cloud Run did not confirm the requested candidate traffic for {name}.")
        smoke()
    except Exception as rollout_error:
        failures = []
        for name in reversed(attempted):
            previous_split = ",".join(f"{revision}={share}" for revision, share in sorted(previous[name].items()))
            try:
                gcloud("services", "update-traffic", name, "--to-revisions", previous_split)
                if traffic_split(gcloud("services", "describe", name)) != previous[name]:
                    raise RuntimeError("Cloud Run did not confirm the previous split.")
            except Exception as restore_error:
                print(f"Failed to restore {name} to {previous_split}: {restore_error}", flush=True)
                failures.append(name)
        if failures:
            raise RuntimeError(f"Traffic restoration needs operator action for {', '.join(failures)}.") from rollout_error
        print("Restored the previous traffic split for every attempted service.", flush=True)
        raise
    return candidates


def smoke_public_routes(api_url: str, web_url: str) -> None:
    urls = (f"{api_url.rstrip('/')}/ready", f"{web_url.rstrip('/')}/")
    for attempt in range(10):
        statuses = [subprocess.run(
            ["curl", "--silent", "--max-time", "10", "--output", os.devnull, "--write-out", "%{http_code}", url],
            capture_output=True, text=True, check=False,
        ) for url in urls]
        if all(status.returncode == 0 and status.stdout == "200" for status in statuses):
            return
        if attempt < 9:
            time.sleep(15)
    raise RuntimeError("Public API readiness and web smoke checks did not both return HTTP 200.")


def main() -> None:
    environment = os.environ["DEPLOY_ENVIRONMENT"]
    percent = int(os.environ["CANARY_PERCENT"])
    if (environment, percent) not in {("staging", 100), ("production", 10)}:
        raise ValueError("Unexpected environment or traffic percentage.")
    project = os.environ["PROJECT_ID"]
    region = os.environ["REGION"]
    api_url = os.environ["PUBLIC_API_URL"]
    web_url = os.environ["PUBLIC_WEB_URL"]
    if not project or not region or not all(url.startswith("https://") for url in (api_url, web_url)):
        raise ValueError("The deployment needs a project, region and HTTPS public origins.")

    def gcloud(*args: str) -> dict[str, Any]:
        result = subprocess.run(
            ["gcloud", "run", *args, "--project", project, "--region", region, "--quiet", "--format=json"],
            stdout=subprocess.PIPE, text=True, check=True,
        )
        return json.loads(result.stdout) if result.stdout.strip() else {}

    repository, tag = os.environ["REPOSITORY"], os.environ["TAG"]
    images = {role: f"{repository}/{role}:{tag}" for role in ("api", "web")}
    suffix = f"r{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
    candidates = roll_out(gcloud, os.environ["PREFIX"], suffix, images, percent, lambda: smoke_public_routes(api_url, web_url))
    print(f"{environment} candidate revisions at {percent}%: {json.dumps(candidates, sort_keys=True)}")
    if environment == "production":
        print("Production stays at 10%. Review candidate telemetry and obtain promotion approval before changing traffic.")


if __name__ == "__main__":
    main()
