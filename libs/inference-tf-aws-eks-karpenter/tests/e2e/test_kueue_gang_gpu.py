"""Gated live E2E — Kueue gang scheduling on GPU (g-tier) nodes.

Validates the full GPU gang scheduling path:

  1. Enable LWS + Kueue on the cluster
  2. Create a 2-pod LWS requesting 1 GPU each, labeled with Kueue queue
  3. Assert: Kueue Workload reaches Admitted=True with the gpu-g flavor
  4. Assert: pods land on Karpenter g-tier nodes (inference/accelerator=nvidia-g)
  5. Assert: both pods reach Running

This exercises the GPU ResourceFlavor injection: Kueue admits → injects
the gpu-g flavor's nodeLabels (inference/accelerator=nvidia-g) + toleration →
pods schedule on the g NodePool.

Uses the g-tier flavor (A10G/L4), NOT the p-tier (gpu-multinode / nvidia-p)
flavor, because p-tier (A100/H100/H200) is scarce and returns InsufficientCapacity
on-demand — that would flake the test on hardware availability, not code. The
gang-scheduling mechanism is identical across tiers; g-tier proves it reliably.

Marked `mutating` — enables the operators and re-applies.
"""

import time

import pytest
from pytest_jupyter_deploy.deployment import EndToEndDeployment

from tests.e2e import _serving_helpers as h

LWS_NAME = "gang-gpu-e2e"
NAMESPACE = "inference"
LOCAL_QUEUE = "inference"


@pytest.mark.mutating
def test_kueue_gang_schedules_on_gpu_nodes(
    e2e_deployment: EndToEndDeployment,
    kubernetes_cluster_login: None,
) -> None:
    """Kueue admits a 2-pod GPU LWS; pods land on Karpenter g-tier nodes."""
    # Enable multinode operators (fixture already deployed the base cluster)
    e2e_deployment.update_override_value("enable_lws", True)
    e2e_deployment.update_override_value("enable_kueue", True)
    e2e_deployment.ensure_deployed_with([], timeout_seconds=1200)

    image = h.client_image(e2e_deployment)

    try:
        h.kubectl("create", "namespace", NAMESPACE, check=False)

        h.apply_resource(
            "gang-scheduling-gpu-lws.yaml",
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
            workloads = h.kubectl("get", "workloads", "-n", NAMESPACE, "-o", "yaml", check=False).stdout
            raise AssertionError(
                f"Kueue Workload never reached Admitted=True for GPU gang\n"
                f"--- Kueue workloads ---\n{workloads[-3000:]}"
            )

        # Wait for both pods Running (includes Karpenter provisioning GPU nodes)
        all_ready = False
        for _ in range(36):  # ~6 min (GPU node provision can take longer)
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
                f"Expected 2 Running GPU pods, got phases: {phases}\n"
                f"--- Pod describe ---\n{desc[-2000:]}"
            )

        # Assert pods landed on Karpenter g-tier GPU nodes (the gpu-g flavor)
        nodes = h.kubectl(
            "get", "pods", "-n", NAMESPACE, "-l", f"app={LWS_NAME}",
            "-o", "jsonpath={.items[*].spec.nodeName}",
        ).stdout.strip().split()
        assert len(nodes) == 2, f"Expected 2 scheduled pods, got: {nodes}"

        for node in nodes:
            accelerator = h.kubectl(
                "get", "node", node,
                "-o", r"jsonpath={.metadata.labels.inference/accelerator}",
            ).stdout.strip()
            assert accelerator == "nvidia-g", (
                f"Pod must run on a g-tier GPU node (gpu-g flavor), "
                f"but {node} has inference/accelerator={accelerator!r}"
            )

    finally:
        h.kubectl(
            "delete", "leaderworkerset", LWS_NAME, "-n", NAMESPACE,
            "--ignore-not-found", check=False,
        )
