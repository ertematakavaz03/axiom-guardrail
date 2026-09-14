"""Start isolated Phase 3 API/worker containers using existing Compose infrastructure."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy.engine import make_url


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["up", "stop"])
    parser.add_argument("--database", default="agentarena_phase3_demo_test")
    args = parser.parse_args()
    names = ["axiom-phase3-api-test", "axiom-phase3-worker-test"]
    if args.action == "stop":
        subprocess.run(["docker", "stop", *names], check=True)
        return
    if not args.database.endswith("_test"):
        raise ValueError("Only an existing dedicated *_test database is allowed")
    config = json.loads(
        subprocess.check_output(["docker", "compose", "config", "--format", "json"], text=True)
    )
    settings = config["services"]["api"]["environment"]
    environment = dict(os.environ)
    overrides = {
        "AGENTARENA_DATABASE_URL": make_url(settings["AGENTARENA_DATABASE_URL"])
        .set(database=args.database)
        .render_as_string(hide_password=False),
        "AGENTARENA_REDIS_URL": settings["AGENTARENA_REDIS_URL"].rsplit("/", 1)[0] + "/14",
        "AGENTARENA_QDRANT_COLLECTION_PREFIX": args.database,
        "AGENTARENA_CORS_ORIGINS": "http://localhost:3001,http://127.0.0.1:3001",
        "LANGFUSE_PUBLIC_KEY": "",
        "LANGFUSE_SECRET_KEY": "",
        "LANGFUSE_HOST": "",
    }
    environment.update(overrides)
    base = ["docker", "compose", "run", "--rm", "--no-deps", "-d"]
    for key in overrides:
        base.extend(["-e", key])
    # Credentials travel through inherited environment, never command text or output.
    api_id = subprocess.check_output(
        [*base, "--name", names[0], "-p", "127.0.0.1:8001:8000", "api"],
        env=environment,
        text=True,
    ).strip()
    image = subprocess.check_output(
        ["docker", "inspect", "--format", "{{.Image}}", api_id], text=True
    ).strip()
    with tempfile.TemporaryDirectory(prefix="axiom-phase3-") as temp:
        override = Path(temp) / "worker.json"
        override.write_text(
            json.dumps(
                {
                    "services": {
                        "worker": {
                            "image": image,
                            "healthcheck": {
                                "test": [
                                    "CMD",
                                    "arq",
                                    "--check",
                                    "apps.api.app.workers.run_worker.WorkerSettings",
                                ],
                                "interval": "15s",
                                "timeout": "10s",
                                "retries": 3,
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                "docker-compose.yml",
                "-f",
                str(override),
                *base[2:],
                "--name",
                names[1],
                "worker",
            ],
            env=environment,
            check=True,
        )
    print(
        "Isolated API: http://localhost:8001; dedicated queue database 14; no production migrations."
    )


if __name__ == "__main__":
    main()
