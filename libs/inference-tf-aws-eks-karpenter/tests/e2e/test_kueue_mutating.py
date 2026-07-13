"""Mutating E2E — enable Kueue + LWS on an existing cluster, then test gang scheduling.

This is a MUTATING test: it modifies the cluster's terraform state by enabling
enable_kueue=true and enable_lws=true on a base cluster that was deployed without
them. This proves the "insert charts by flags" workflow:

  1. Base cluster deployed (no Kueue/LWS)
  2. Update overrides: enable_lws=true, enable_kueue=true
  3. Re-apply (terraform apply adds LWS + Kueue controllers + queue config)
  4. Create a gang-scheduled LWS workload in the inference namespace
  5. Assert: Kueue Workload reaches Admitted=True, pods Running

Run with: just test-e2e-eks-karpenter <project> "test_kueue_mutating" "full-deploy=true,mutate=true"
"""

import time

import pytest
from pytest_jupyter_deploy.deployment import EndToEndDeployment

from tests.e2e import _serving_helpers as h

LWS_NAME = "gang-e2e"
NAMESPACE = "inference"
LOCAL_QUEUE = "inference"


@pytest.mark.mutating
def test_enable_kueue_lws_then_gang_schedule(
    e2e_deployment: EndToEndDeployment,
    kubernetes_cluster_login: None,
) -> None:
    """Enable Kueue+LWS on base cluster via flags, then validate gang scheduling."""
    # Phase 1: Mutate the cluster — add Kueue + LWS operators
    e2e_deployment.ensure_deployed()
    e2e_deployment.update_override_value("enable_lws", True)
    e2e_deployment.update_override_value("enable_kueue", True)
    e2e_deployment.ensure_deployed_with([], timeout_seconds=900)

    # Verify the operators are running
    lws_pods = h.kubectl(
        "get", "pods", "-n", "lws-system", "--no-headers", check=False
    ).stdout.strip()
    assert lws_pods, "LWS controller pod should be running after enable_lws=true"

    kueue_pods = h.kubectl(
        "get", "pods", "-n", "kueue-system", "--no-headers", check=False
    ).stdout.strip()
    assert kueue_pods, "Kueue controller pod should be running after enable_kueue=true"

    # Phase 2: Test gang scheduling
    image = h.client_image(e2e_deployment)

    try:
        # Ensure namespace exists (the LocalQueue lives here)
        h.kubectl("create", "namespace", NAMESPACE, check=False)

        # Apply LWS from resources/
        h.apply_resource(
            "gang-scheduling-lws.yaml",
            image=image,
            namespace=NAMESPACE,
            queue_name=LOCAL_QUEUE,
        )

        # Assert Kueue Workload reaches Admitted=True
        admitted = False
        for _ in range(30):
            result = h.kubectl(
                "get", "workloads", "-n", NAMESPACE,
                "-o", "jsonpath={.items[0].status.conditions[?(@.type=='Admitted')].status}",
                check=False,
            )
            if result.stdout.strip() == "True":
                admitted = True
                break
            time.sleep(10)

        if not admitted:
            workloads = h.kubectl("get", "workloads", "-n", NAMESPACE, "-o", "wide", check=False).stdout
            raise AssertionError(
                f"Kueue Workload never reached Admitted=True after enabling via flags\n"
                f"--- Kueue workloads ---\n{workloads}"
            )

        # Wait for pods Running
        all_ready = False
        for _ in range(18):
            result = h.kubectl(
                "get", "pods", "-n", NAMESPACE, "-l", f"app={LWS_NAME}",
                "-o", "jsonpath={.items[*].status.phase}",
                check=False,
            )
            phases = result.stdout.strip().split()
            if len(phases) == 2 and all(p == "Running" for p in phases):
                all_ready = True
                break
            time.sleep(10)

        if not all_ready:
            desc = h.kubectl(
                "describe", "pods", "-n", NAMESPACE, "-l", f"app={LWS_NAME}", check=False
            ).stdout
            raise AssertionError(
                f"Expected 2 Running pods, got phases: {phases}\n"
                f"--- Pod describe ---\n{desc[-2000:]}"
            )

        nodes = h.kubectl(
            "get", "pods", "-n", NAMESPACE, "-l", f"app={LWS_NAME}",
            "-o", "jsonpath={.items[*].spec.nodeName}",
        ).stdout.strip().split()
        assert len(nodes) == 2, f"Expected 2 scheduled pods, got: {nodes}"

    finally:
        # Deleting the LWS cascades to all owned pods (same as Deployment/StatefulSet)
        h.kubectl(
            "delete", "leaderworkerset", LWS_NAME, "-n", NAMESPACE,
            "--ignore-not-found", check=False,
        )
