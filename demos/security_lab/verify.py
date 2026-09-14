"""Run migrations and tests against an explicitly isolated PostgreSQL database."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

from apps.api.app.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="agentarena_phase3_test")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--migration-roundtrip", action="store_true")
    parser.add_argument(
        "--docker",
        action="store_true",
        help="Use Compose networking, avoiding host port collisions",
    )
    args = parser.parse_args()
    if not args.database.endswith("_test") or args.database == "agentarena":
        raise ValueError("Verification requires a dedicated *_test database")
    if args.docker:
        install_and_run = (
            "import subprocess,sys,tomllib; "
            "deps=tomllib.load(open('pyproject.toml','rb'))['project']['optional-dependencies']['dev']; "
            "subprocess.check_call([sys.executable,'-m','pip','install','-q',*deps]); "
            "subprocess.check_call([sys.executable,'-m','demos.security_lab.verify',*sys.argv[1:]])"
        )
        command = [
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "-v",
            f"{Path.cwd()}:/app",
            "api",
            "python",
            "-c",
            install_and_run,
            *[arg for arg in sys.argv[1:] if arg != "--docker"],
        ]
        raise SystemExit(subprocess.run(command, check=False).returncode)
    url = make_url(Settings().database_url).set(database=args.database)
    os.environ["AGENTARENA_DATABASE_URL"] = url.render_as_string(hide_password=False)
    # Separate queue and vector namespace from the persisted product benchmark.
    os.environ["AGENTARENA_REDIS_URL"] = Settings().redis_url.rsplit("/", 1)[0] + "/15"
    os.environ["AGENTARENA_QDRANT_COLLECTION_PREFIX"] = args.database
    commands = [["alembic", "upgrade", "head"]]
    if args.migration_roundtrip:
        commands.extend([["alembic", "downgrade", "20260829_0002"], ["alembic", "upgrade", "head"]])
    commands.append(
        ["pytest", "-q", "--tb=short"]
        if args.full
        else ["pytest", "tests/integration/test_phase3_security_e2e.py", "-q", "--tb=short"]
    )
    for command in commands:
        result = subprocess.run([sys.executable, "-m", *command], check=False)
        if result.returncode:
            raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
