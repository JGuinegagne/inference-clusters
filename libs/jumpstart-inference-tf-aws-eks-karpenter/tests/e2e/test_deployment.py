"""E2E deployment smoke tests for the AWS EKS Karpenter template.

These tests require AWS credentials and provision real infrastructure. They are
marked `full_deployment` so they only run when deploying from scratch (not against
an existing project) — pass `full-deploy=true` to the justfile recipe to include them.
"""

import pytest
from pytest_jupyter_deploy.deployment import EndToEndDeployment


@pytest.mark.full_deployment
def test_cluster_deploys(e2e_deployment: EndToEndDeployment) -> None:
    """The template deploys end-to-end and exposes the expected outputs."""
    e2e_deployment.ensure_deployed()

    deployment_id = e2e_deployment.cli.run_command(
        ["jupyter-deploy", "show", "--output", "deployment_id", "--text"]
    ).stdout.strip()
    assert deployment_id, "deployment_id output should be populated after deploy"

    region = e2e_deployment.cli.run_command(["jupyter-deploy", "show", "--output", "region", "--text"]).stdout.strip()
    assert region, "region output should be populated after deploy"
