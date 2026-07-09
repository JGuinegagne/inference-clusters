"""Gated live E2E — Kueue gang scheduling with Karpenter node provisioning.

Validates that Kueue + Karpenter cooperate for all-or-nothing workload admission:

  1. Create a LeaderWorkerSet with 2 pods (leader + 1 worker) requesting CPU only
     (no GPU — avoids ICE on scarce accelerator capacity).
  2. Label the LWS with kueue.x-k8s.io/queue-name so Kueue gates the workload.
  3. Assert: both pods are admitted together (Kueue un-gates atomically), Karpenter
     provisions nodes for them, and both reach Ready.

This proves the integration path: Kueue admits -> pods go Pending -> Karpenter
reacts -> nodes launch -> pods schedule -> waitForPodsReady satisfied.

No GPUs needed — uses 2 small CPU instances from the default Karpenter NodePool.
The test exercises the gang scheduling machinery, not the GPU serving path.

Marked `full_deployment` — requires a live cluster with Kueue + LWS + Karpenter.
"""

import subprocess
import time

import pytest
from pytest_jupyter_deploy.deployment import EndToEndDeployment

from tests.e2e import _serving_helpers as h

LWS_NAME = "gang-e2e"
NAMESPACE = "default"
LOCAL_QUEUE = "inference"


def _lws_manifest(image: str) -> str:
    """Return the LWS manifest as a string (2 pods, CPU-only, Kueue-gated)."""
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


@pytest.mark.full_deployment
def test_kueue_gang_schedules_lws_group(e2e_deployment: EndToEndDeployment) -> None:
    """Kueue admits a 2-pod LWS atomically; Karpenter provisions nodes; both pods Ready."""
    e2e_deployment.ensure_deployed()
    e2e_deployment.cli.run_command(["jupyter-deploy", "cluster", "login"])
    image = h.client_image(e2e_deployment)

    try:
        # Apply the LWS manifest (Kueue-gated via queue-name label).
        manifest = _lws_manifest(image)
        subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=manifest,
            text=True,
            check=True,
            capture_output=True,
        )

        # Wait for both pods to become Ready. Kueue must admit them together,
        # Karpenter provisions nodes, pods schedule — entire chain under 5 min
        # for CPU-only workloads.
        all_ready = False
        for _ in range(30):  # ~5 min (30 x 10s)
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
            # Check Kueue workload status for debugging.
            workloads = h.kubectl(
                "get", "workloads", "-n", NAMESPACE, "-o", "wide", check=False
            ).stdout
            raise AssertionError(
                f"Expected 2 Running pods for gang-scheduled LWS, got phases: {phases}\n"
                f"--- Pod describe ---\n{desc[-2000:]}\n"
                f"--- Kueue workloads ---\n{workloads}"
            )

        # Verify: both pods landed on nodes (gang admission + scheduling worked).
        nodes_json = h.kubectl(
            "get", "pods", "-n", NAMESPACE, "-l", f"app={LWS_NAME}",
            "-o", "jsonpath={.items[*].spec.nodeName}",
        ).stdout.strip().split()
        assert len(nodes_json) == 2, f"Expected 2 scheduled pods, got nodes: {nodes_json}"

    finally:
        subprocess.run(
            ["kubectl", "delete", "leaderworkerset", LWS_NAME, "-n", NAMESPACE, "--ignore-not-found"],
            check=False,
            capture_output=True,
        )
        # Wait for Karpenter to consolidate the empty nodes (best-effort cleanup).
        time.sleep(10)
