#!/usr/bin/env python3
"""Discover and restore the CI infrastructure project (tf-aws-iam-ci) from the S3 store.

Usage: scripts/ci_restore.py [ci-dir]

Restores the one tf-aws-iam-ci-* project so CI jobs can read its outputs (ECR repo URL,
test-results bucket, etc.). The tf-aws-iam-ci deploy declares GitHub bot/oauth sensitive
variables (throwaway placeholders — this repo has no GitHub apps), so after restoring the
project we un-mask them via `jd config --restore-secret <name>`.

We restore each readable secret individually rather than with `--restore-secrets` (restore
all): the CI e2e role is intentionally denied read on github_bot_account_recovery_codes
(maintainer-only), so restore-all would 403 on it. That secret is not needed for E2E
operations, so we skip it and keep it masked.

Uses the local scripts/ci_helpers.py to drive the published `jd` CLI installed in
this workspace.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from ci_helpers import run_jd, run_jd_config

CI_PROJECT_PREFIX = "tf-aws-iam-ci-"


def discover_project_id() -> str:
    result = run_jd(["projects", "list", "--store-type", "s3-only", "--text"], capture=True)
    matches = [
        line.strip() for line in result.stdout.strip().splitlines() if line.strip().startswith(CI_PROJECT_PREFIX)
    ]

    if not matches:
        print(f"Error: No CI project found in the S3 store (no {CI_PROJECT_PREFIX}* project)")
        sys.exit(1)
    if len(matches) > 1:
        print("Error: Multiple CI projects found in the S3 store:")
        for m in matches:
            print(f"  {m}")
        print(f"Expected exactly one {CI_PROJECT_PREFIX}* project.")
        sys.exit(1)

    return matches[0]


def restore_project(project_id: str, ci_dir: Path) -> None:
    if ci_dir.exists():
        shutil.rmtree(ci_dir)
    print(f"Restoring CI project {project_id} to {ci_dir}...")
    run_jd(["init", str(ci_dir), "--restore-project", project_id, "--store-type", "s3-only"])


def restore_secrets(ci_dir: Path) -> None:
    # Restore every sensitive var the CI e2e role can read; github_bot_account_recovery_codes
    # is maintainer-only (explicit deny), so keep it masked rather than 403 on --restore-secrets.
    restore_names = [
        "github_bot_account_password",
        "github_bot_account_totp_secret",
        *(f"github_oauth_app_client_secret_{i}" for i in range(1, 7)),
    ]

    config_args: list[str] = []
    for name in restore_names:
        config_args.extend(["--restore-secret", name])
    config_args.extend(["--github-bot-account-recovery-codes", "****"])

    run_jd_config(config_args, str(ci_dir))


def main() -> None:
    ci_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sandbox-ci")

    print("Discovering CI project in the S3 store...")
    project_id = discover_project_id()
    print(f"Found CI project: {project_id}")

    restore_project(project_id, ci_dir)

    # Re-populate masked secrets so `jd show` outputs and the backend are usable.
    print("\nRestoring secrets and configuring...")
    restore_secrets(ci_dir)

    print(f"\nCI project restored and configured at {ci_dir}")


if __name__ == "__main__":
    main()
