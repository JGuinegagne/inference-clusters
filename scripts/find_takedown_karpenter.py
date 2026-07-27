#!/usr/bin/env python3
"""Take down and delete EKS Karpenter e2e project(s) from the S3 store.

Usage:
    scripts/find_takedown_karpenter.py <project-dir> [--deployment-id <id>]

With --deployment-id: reap only that one project (the standard e2e flow tears down its
OWN deployment, so parallel runs never touch each other).

Without --deployment-id: reap ALL `tf-aws-eks-karpenter-*` projects — the nuclear option
for the standalone cleanup workflow, to clear orphans from interrupted runs. NOT used by
the standard e2e flow.

For each target: restore locally, `jd down -y`, delete from the store. Exits 0 if there
is nothing to take down. Uses the local scripts/ci_helpers.py to drive the published `jd`
CLI installed in this workspace.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from ci_helpers import is_project_deployed
from ci_restore_karpenter import (
    KARPENTER_PROJECT_PREFIX,
    list_karpenter_projects,
    restore_project,
    restore_secrets,
)


def takedown_project(project_dir: Path) -> None:
    print(f"Taking down deployment in {project_dir}...")
    result = subprocess.run(["uv", "run", "jd", "down", "-y", "-v"], cwd=project_dir)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def delete_project_from_store(project_id: str) -> None:
    print(f"Deleting project {project_id} from the S3 store...")
    subprocess.run(
        ["uv", "run", "jd", "projects", "delete", project_id, "--store-type", "s3-only", "-y"],
        check=True,
    )


def reap(project_id: str, project_dir: Path) -> None:
    print(f"\n=== {project_id} ===")
    restore_project(project_id, project_dir)

    if not is_project_deployed(str(project_dir)):
        print(f"  {project_id} has no live infrastructure (empty state) — skipping jd down.")
        delete_project_from_store(project_id)
        print(f"  Stale project {project_id} deleted from store.")
        return

    # Takedown uses destroy.tfvars and never reads a restored secret value, so a
    # missing secret must not block teardown.
    print("  Restoring secrets from the cloud provider...")
    restore_secrets(project_dir, required=False)

    takedown_project(project_dir)
    delete_project_from_store(project_id)
    print(f"  Project {project_id} taken down and deleted.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Take down + delete karpenter e2e project(s).")
    parser.add_argument("project_dir", nargs="?", default="sandbox-e2e", help="Directory to restore into")
    parser.add_argument(
        "--deployment-id",
        default=None,
        help="Reap only this deployment's project (default: reap ALL — nuclear cleanup)",
    )
    args = parser.parse_args()

    project_dir = Path(args.project_dir)

    targets = [f"{KARPENTER_PROJECT_PREFIX}{args.deployment_id}"] if args.deployment_id else list_karpenter_projects()

    if not targets:
        print(f"No {KARPENTER_PROJECT_PREFIX}* project found — nothing to take down.")
        return

    scope = "own deployment" if args.deployment_id else f"ALL ({len(targets)} project(s))"
    print(f"Reaping {scope}: {targets}")

    for project_id in targets:
        reap(project_id, project_dir)

    print("\nDone.")


if __name__ == "__main__":
    main()
