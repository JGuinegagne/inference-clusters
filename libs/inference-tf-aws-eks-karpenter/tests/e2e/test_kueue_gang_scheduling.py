"""Gated live E2E — Kueue gang scheduling with Karpenter node provisioning.

Validates that Kueue + Karpenter cooperate for all-or-nothing workload admission:

  1. Enable LWS + Kueue on the cluster (mutating re-apply)
  2. Create a LeaderWorkerSet with 2 pods (leader + 1 worker) requesting CPU only
     (no GPU — avoids ICE on scarce accelerator capacity)
  3. Label the LWS with kueue.x-k8s.io/queue-name targeting the inference LocalQueue
  4. Assert: Kueue Workload reaches Admitted=True, both pods Running

This proves the integration path: Kueue admits -> pods go Pending -> Karpenter
reacts -> nodes launch -> pods schedule -> waitForPodsReady satisfied.

No GPUs needed — uses 2 small CPU instances from the default Karpenter NodePool.
Marked `full_deployment` — requires a live cluster.
"""

import time

import pytest
from pytest_jupyter_deploy.deployment import EndToEndDeployment

from tests.e2e import _serving_helpers as h

LWS_NAME = "gang-e2e"
NAMESPACE = "inference"
LOCAL_QUEUE = "inference"


@pytest.mark.full_deployment
def test_kueue_gang_schedules_lws_group(
    e2e_deployment: EndToEndDeployment,
    kubernetes_cluster_login: None,
) -> None:
    """Kueue admits a 2-pod LWS atomically; Karpenter provisions nodes; both pods Ready."""
    # Enable multinode operators on the base cluster
    e2e_deployment.ensure_deployed()
    e2e_deployment.update_override_value("enable_lws", True)
    e2e_deployment.update_override_value("enable_kueue", True)
    e2e_deployment.ensure_deployed_with([], timeout_seconds=900)

    image = h.client_image(e2e_deployment)

    try:
        # Ensure namespace exists (the LocalQueue lives in "inference")
        h.kubectl("create", "namespace", NAMESPACE, check=False)

        # Apply the LWS manifest from resources/ (Kueue-gated via queue-name label)
        h.apply_resource(
            "gang-scheduling-lws.yaml",
            image=image,
            namespace=NAMESPACE,
            queue_name=LOCAL_QUEUE,
        )

        # Wait for Kueue to admit the workload (Admitted=True)
        admitted = False
        for _ in range(30):  # ~5 min
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
                f"Kueue Workload never reached Admitted=True\n"
                f"--- Kueue workloads ---\n{workloads}"
            )

        # Wait for both pods to reach Running
        all_ready = False
        for _ in range(18):  # ~3 min (admission already happened)
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

        # Verify pods are scheduled on nodes
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
