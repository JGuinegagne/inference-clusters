#!/usr/bin/env python3
"""Find and restore an EKS Karpenter e2e project from the S3 store.

Usage:
    scripts/ci_restore_karpenter.py <project-dir> [--deployment-id <id>]

The karpenter template has no ingress/subdomain/OAuth (unlike jupyter-deploy's
eks-oidc, whose ci_restore_eks.py matches by an OAuth-derived subdomain). e2e runs are
self-isolated by `random_id`, so multiple `tf-aws-eks-karpenter-<deployment_id>` projects
can coexist in the store (parallel PR runs). Pass --deployment-id to restore a specific
one; with no id, restore the single project if exactly one exists (local/manual use).

Uses the local scripts/ci_helpers.py to drive the published `jd` CLI installed in this
workspace.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from ci_helpers import is_project_deployed, run_jd, run_jd_config

KARPENTER_PROJECT_PREFIX = "tf-aws-eks-karpenter-"


def list_karpenter_projects() -> list[str]:
    result = run_jd(["projects", "list", "--store-type", "s3-only", "--text"], capture=True)
    return [
        line.strip() for line in result.stdout.strip().splitlines() if line.strip().startswith(KARPENTER_PROJECT_PREFIX)
    ]


def resolve_project_id(deployment_id: str | None, *, allow_missing: bool = False) -> str | None:
    """Resolve the target project id: the one matching deployment_id, else the sole project."""
    if deployment_id:
        return f"{KARPENTER_PROJECT_PREFIX}{deployment_id}"

    matches = list_karpenter_projects()
    if not matches:
        if allow_missing:
            return None
        print(f"Error: No {KARPENTER_PROJECT_PREFIX}* project found in the S3 store")
        sys.exit(1)
    if len(matches) > 1:
        print(f"Error: Multiple {KARPENTER_PROJECT_PREFIX}* projects found — pass --deployment-id to pick one:")
        for m in matches:
            print(f"  {m}")
        sys.exit(1)
    return matches[0]


def restore_project(project_id: str, project_dir: Path) -> None:
    if project_dir.exists():
        shutil.rmtree(project_dir)
    print(f"Restoring project {project_id} to {project_dir}...")
    run_jd(["init", str(project_dir), "--restore-project", project_id, "--store-type", "s3-only"])


def restore_secrets(project_dir: Path, required: bool = True) -> None:
    """Restore masked secrets via jd config --restore-secrets.

    With required=False a failure is logged and ignored — safe for the takedown path,
    where `jd down` uses destroy.tfvars and never reads a restored secret value.
    """
    if not run_jd_config(["--restore-secrets"], str(project_dir), check=required) and not required:
        print("⚠️  Secret restore failed — continuing (not required for takedown).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore a karpenter e2e project from the S3 store.")
    parser.add_argument("project_dir", nargs="?", default="sandbox-e2e", help="Directory to restore into")
    parser.add_argument("--deployment-id", default=None, help="Restore the project for this deployment id")
    args = parser.parse_args()

    project_dir = Path(args.project_dir)

    print("Resolving the karpenter e2e project in the S3 store...")
    project_id = resolve_project_id(args.deployment_id)
    assert project_id is not None
    print(f"  Target project: {project_id}")

    restore_project(project_id, project_dir)

    if not is_project_deployed(str(project_dir)):
        print(
            f"\nError: Project '{project_id}' exists in the S3 store but has no live infrastructure.",
            file=sys.stderr,
        )
        print(
            "Run the fresh deploy workflow to recreate it, or delete the stale entry with:\n"
            f"  uv run jd projects delete {project_id} --store-type s3-only -y",
            file=sys.stderr,
        )
        sys.exit(1)

    print("\nRestoring secrets from the cloud provider...")
    restore_secrets(project_dir)

    print(f"\nKarpenter e2e project restored at {project_dir}")


if __name__ == "__main__":
    main()
