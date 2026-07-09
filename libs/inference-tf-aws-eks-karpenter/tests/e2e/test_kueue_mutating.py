"""Mutating E2E — enable Kueue + LWS on an existing cluster, then test gang scheduling.

This is a MUTATING test: it modifies the cluster's terraform state by enabling
enable_kueue=true and enable_lws=true on a base cluster that was deployed without
them. This proves the "insert charts by flags" workflow:

  1. Base cluster deployed (no Kueue/LWS)
  2. Update overrides: enable_lws=true, enable_kueue=true
  3. Re-apply (terraform apply adds LWS + Kueue controllers + queue config)
  4. Create a gang-scheduled LWS workload
  5. Assert: Kueue admits, Karpenter provisions, pods reach Running

Run with: just test-e2e-eks-karpenter <project> "test_kueue_mutating" "full-deploy=true,mutate=true"

The base cluster must already exist (deployed with full-deploy=true in a prior run,
or an existing project). This test only adds the multinode operators on top.
"""

import subprocess
import time

import pytest
from pytest_jupyter_deploy.deployment import EndToEndDeployment

from tests.e2e import _serving_helpers as h

LWS_NAME = "gang-mutate-e2e"
NAMESPACE = "default"
LOCAL_QUEUE = "inference"


def _lws_manifest(image: str) -> str:
    """2-pod CPU-only LWS with Kueue queue label."""
    return f"""\
apiVersion: leaderworkerset.x-k8s.io/v1
kind: LeaderWorkerSet
metadata:
  name: {LWS_NAME}
  namespace: {NAMESPACE}
  labels:
    kueue.x-k8s.io/queue-name: {LOCAL_QUEUE}
spec:
  replicas: 1
  leaderWorkerTemplate:
    size: 2
    restartPolicy: RecreateGroupOnPodRestart
    leaderTemplate:
      metadata:
        labels:
          app: {LWS_NAME}
          role: leader
      spec:
        terminationGracePeriodSeconds: 5
        containers:
          - name: worker
            image: {image}
            command: ["sh", "-c", "echo leader ready; sleep 600"]
            resources:
              requests:
                cpu: "250m"
                memory: "128Mi"
    workerTemplate:
      metadata:
        labels:
          app: {LWS_NAME}
          role: worker
      spec:
        terminationGracePeriodSeconds: 5
        containers:
          - name: worker
            image: {image}
            command: ["sh", "-c", "echo worker ready; sleep 600"]
            resources:
              requests:
                cpu: "250m"
                memory: "128Mi"
"""


@pytest.mark.mutating
def test_enable_kueue_lws_then_gang_schedule(e2e_deployment: EndToEndDeployment) -> None:
    """Enable Kueue+LWS on base cluster via flags, then validate gang scheduling."""
    # Phase 1: Mutate the cluster — add Kueue + LWS operators
    e2e_deployment.ensure_deployed()  # ensure base cluster exists

    e2e_deployment.update_override_value("enable_lws", True)
    e2e_deployment.update_override_value("enable_kueue", True)

    # Re-deploy with the new flags (terraform apply adds LWS + Kueue + queue config)
    e2e_deployment.ensure_deployed_with([], timeout_seconds=900)

    # Login to the updated cluster
    e2e_deployment.cli.run_command(["jupyter-deploy", "cluster", "login"])

    # Verify the operators are running
    lws_pods = h.kubectl(
        "get", "pods", "-n", "lws-system", "--no-headers", check=False
    ).stdout.strip()
    assert lws_pods, "LWS controller pod should be running after enable_lws=true"

    kueue_pods = h.kubectl(
        "get", "pods", "-n", "kueue-system", "--no-headers", check=False
    ).stdout.strip()
    assert kueue_pods, "Kueue controller pod should be running after enable_kueue=true"

    # Phase 2: Test gang scheduling (same as test_kueue_gang_scheduling but
    # proving the "insert by flags" path works)
    image = h.client_image(e2e_deployment)

    try:
        manifest = _lws_manifest(image)
        subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=manifest,
            text=True,
            check=True,
            capture_output=True,
        )

        # Wait for both pods to reach Running
        all_ready = False
        for _ in range(30):  # ~5 min
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
            workloads = h.kubectl(
                "get", "workloads", "-n", NAMESPACE, "-o", "wide", check=False
            ).stdout
            raise AssertionError(
                f"Expected 2 Running pods after enabling Kueue+LWS via flags, "
                f"got phases: {phases}\n"
                f"--- Pod describe ---\n{desc[-2000:]}\n"
                f"--- Kueue workloads ---\n{workloads}"
            )

        # Verify pods are scheduled
        nodes = h.kubectl(
            "get", "pods", "-n", NAMESPACE, "-l", f"app={LWS_NAME}",
            "-o", "jsonpath={.items[*].spec.nodeName}",
        ).stdout.strip().split()
        assert len(nodes) == 2, f"Expected 2 scheduled pods, got: {nodes}"

    finally:
        subprocess.run(
            ["kubectl", "delete", "leaderworkerset", LWS_NAME, "-n", NAMESPACE, "--ignore-not-found"],
            check=False,
            capture_output=True,
        )
        time.sleep(10)
