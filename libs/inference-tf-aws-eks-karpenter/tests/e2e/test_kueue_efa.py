"""Gated live E2E — EFA multi-node gang scheduling with same-AZ co-location.

Proves the EFA path end-to-end, which the g-tier gang test cannot:

  1. Enable LWS + Kueue + EFA on the cluster
  2. Create a 2-pod LWS on g6e.24xlarge, each requesting vpc.amazonaws.com/efa: 1,
     with a podAffinity rule pinning both pods to the same AZ
  3. Assert: Kueue Workload reaches Admitted=True
  4. Assert: both pods reach Running with an EFA interface allocated
  5. Assert: both pods land in the SAME AZ (podAffinity — EFA can't cross AZ)

Co-location is via podAffinity on topology.kubernetes.io/zone (a GA scheduler
primitive that works with Karpenter JIT provisioning), NOT Kueue TAS — TAS
pre-computes fit over existing nodes, which don't exist at admission time on
Karpenter, and its ProvisioningRequest path is Cluster-Autoscaler-only.

Why g6e.24xlarge: it is the smallest EFA-capable instance (1 EFA interface,
~$15/hr) vs p5.48xlarge (32 EFA, ~$98/hr, frequently InsufficientCapacity).
It proves the EFA mechanism — device plugin advertises the resource, Kueue
admits, pods schedule with EFA allocated + AZ co-located — without the p5
capacity flakiness. It does NOT prove the 32-rail p5 topology.

Marked `mutating` — enables operators + re-applies. Opt-in: g6e.24xlarge is a
standing cost while up, so this is not part of the default gang-mechanics test.
"""

import time

import pytest
from pytest_jupyter_deploy.deployment import EndToEndDeployment

from tests.e2e import _serving_helpers as h

LWS_NAME = "efa-gang-e2e"
NAMESPACE = "inference"
LOCAL_QUEUE = "inference"


@pytest.mark.mutating
def test_kueue_efa_multinode_gang(
    e2e_deployment: EndToEndDeployment,
    kubernetes_cluster_login: None,
) -> None:
    """EFA gang: Kueue admits, pods get EFA interfaces, podAffinity co-locates in one AZ."""
    # Enable LWS + Kueue + EFA (fixture already deployed the base cluster)
    e2e_deployment.update_override_value("enable_lws", True)
    e2e_deployment.update_override_value("enable_kueue", True)
    e2e_deployment.update_override_value("enable_efa", True)
    e2e_deployment.ensure_deployed_with([], timeout_seconds=1200)

    image = h.client_image(e2e_deployment)

    try:
        h.kubectl("create", "namespace", NAMESPACE, check=False)

        h.apply_resource(
            "efa-gang-lws.yaml",
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
                f"Kueue Workload never reached Admitted=True for EFA gang\n"
                f"--- Kueue workloads ---\n{workloads[-3000:]}"
            )

        # Wait for both pods Running (g6e.24xlarge provisioning can take a few min)
        all_ready = False
        for _ in range(42):  # ~7 min
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
                f"Expected 2 Running EFA pods, got phases: {phases}\n"
                f"--- Pod describe ---\n{desc[-2000:]}"
            )

        # Assert both pods got an EFA interface allocated (limits reflect the request)
        efa_limits = h.kubectl(
            "get", "pods", "-n", NAMESPACE, "-l", f"app={LWS_NAME}",
            "-o", r"jsonpath={.items[*].spec.containers[0].resources.limits.vpc\.amazonaws\.com/efa}",
        ).stdout.strip().split()
        assert efa_limits == ["1", "1"], (
            f"Both pods must have 1 EFA interface allocated, got: {efa_limits}"
        )

        # Assert podAffinity co-located both pods in the same AZ (EFA cannot cross AZ)
        nodes = h.kubectl(
            "get", "pods", "-n", NAMESPACE, "-l", f"app={LWS_NAME}",
            "-o", "jsonpath={.items[*].spec.nodeName}",
        ).stdout.strip().split()
        assert len(nodes) == 2, f"Expected 2 scheduled pods, got: {nodes}"

        zones = []
        for node in nodes:
            zone = h.kubectl(
                "get", "node", node,
                "-o", r"jsonpath={.metadata.labels.topology\.kubernetes\.io/zone}",
            ).stdout.strip()
            zones.append(zone)
        assert zones[0] == zones[1] and zones[0], (
            f"podAffinity must co-locate both pods in the same AZ for EFA, got zones: {zones}"
        )

    finally:
        # Deleting the LWS cascades to owned pods (ownerReferences)
        h.kubectl(
            "delete", "leaderworkerset", LWS_NAME, "-n", NAMESPACE,
            "--ignore-not-found", check=False,
        )
