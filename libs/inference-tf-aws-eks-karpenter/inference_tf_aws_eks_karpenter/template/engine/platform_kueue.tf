# === Kueue — admission control + gang scheduling for multi-node inference ===
#
# Kueue gates LWS workloads behind quota — the entire group is admitted
# atomically or stays suspended. Two features are on when enabled:
#
# 1. Prometheus ServiceMonitor: queue health visibility.
# 2. waitForPodsReady: evicts workload on partial provisioning failure — the
#    reactive backstop for the Karpenter capacity pre-check gap (Karpenter can't
#    confirm hardware exists before Kueue admits; ProvisioningRequest is
#    Cluster-Autoscaler-only, karpenter#742/#2571).
#
# NOT TAS: TopologyAwareScheduling pre-computes topology fit over existing nodes,
# which is meaningless on Karpenter JIT provisioning. EFA same-AZ co-location is
# enforced via podAffinity in the workload spec instead.
#
# Placement: controller on the tainted system NG.
# Images/chart: OCI on registry.k8s.io (pull-through, no vendoring).

locals {
  kueue_namespace = "kueue-system"
}

resource "helm_release" "kueue" {
  count = var.enable_kueue ? 1 : 0

  name             = "kueue"
  repository       = "oci://registry.k8s.io/kueue/charts"
  chart            = "kueue"
  version          = var.kueue_chart_version
  namespace        = local.kueue_namespace
  create_namespace = true

  set = [
    # Repin controller image to pull-through URI.
    { name = "controllerManager.manager.image.repository", value = "${local.ecr_registry}/registry-k8s/kueue/kueue" },
    { name = "controllerManager.manager.image.tag", value = "v${var.kueue_chart_version}" },

    # NOTE: TopologyAwareScheduling is intentionally NOT enabled. TAS pre-computes
    # topology fit over existing nodes, which is meaningless on Karpenter's
    # just-in-time provisioning (no nodes at admission time). EFA same-AZ
    # co-location is enforced via podAffinity in the workload spec instead.

    # Prometheus ServiceMonitor (top-level toggle)
    { name = "enablePrometheus", value = "true" },

    # System NG placement (controllerManager.nodeSelector / controllerManager.tolerations)
    { name = "controllerManager.nodeSelector.inference/role", value = "system" },
    { name = "controllerManager.tolerations[0].key", value = "inference/role" },
    { name = "controllerManager.tolerations[0].operator", value = "Equal" },
    { name = "controllerManager.tolerations[0].value", value = "system" },
    { name = "controllerManager.tolerations[0].effect", value = "NoSchedule" },
  ]

  # managerConfig.controllerManagerConfigYaml is an opaque YAML STRING — not
  # deep-merged. We must carry the FULL default (including integrations.frameworks
  # which registers LWS) and append waitForPodsReady. Verified with:
  #   helm template kueue ... | grep -E 'leaderworkerset|waitForPodsReady'
  values = [yamlencode({
    managerConfig = {
      controllerManagerConfigYaml = <<-YAML
        apiVersion: config.kueue.x-k8s.io/v1beta2
        kind: Configuration
        health:
          healthProbeBindAddress: :8081
        metrics:
          bindAddress: :8443
        webhook:
          port: 9443
        leaderElection:
          leaderElect: true
          resourceName: c1f6bfd2.kueue.x-k8s.io
        controller:
          groupKindConcurrency:
            Job.batch: 5
            Pod: 5
            Workload.kueue.x-k8s.io: 5
            LocalQueue.kueue.x-k8s.io: 1
            ClusterQueue.kueue.x-k8s.io: 1
            ResourceFlavor.kueue.x-k8s.io: 1
        clientConnection:
          qps: 50
          burst: 100
        waitForPodsReady:
          timeout: 15m
          recoveryTimeout: 3m
          blockAdmission: true
          requeuingStrategy:
            timestamp: Eviction
            backoffLimitCount: 3
            backoffBaseSeconds: 60
            backoffMaxSeconds: 3600
        integrations:
          frameworks:
            - "batch/job"
            - "kubeflow.org/mpijob"
            - "ray.io/rayjob"
            - "ray.io/rayservice"
            - "ray.io/raycluster"
            - "jobset.x-k8s.io/jobset"
            - "trainer.kubeflow.org/trainjob"
            - "kubeflow.org/paddlejob"
            - "kubeflow.org/pytorchjob"
            - "kubeflow.org/tfjob"
            - "kubeflow.org/xgboostjob"
            - "kubeflow.org/jaxjob"
            - "workload.codeflare.dev/appwrapper"
            - "pod"
            - "deployment"
            - "statefulset"
            - "leaderworkerset.x-k8s.io/leaderworkerset"
      YAML
    }
  })]

  depends_on = [
    null_resource.cluster_addons,
    null_resource.pullthrough_ready,
    module.node_group,
    helm_release.kube_prometheus_stack,
    helm_release.leader_worker_set,
  ]
}

# --- Kueue queue configuration (charts/kueue) ---
#
# First-party local chart: ResourceFlavors, ClusterQueue, LocalQueue, namespace.
# Installed as a helm_release (not kubernetes_manifest) because kubernetes_manifest
# requires a live cluster connection at plan time.
resource "helm_release" "kueue_config" {
  count     = var.enable_kueue ? 1 : 0
  name      = "kueue-config"
  chart     = "${path.module}/../charts/kueue"
  namespace = local.kueue_namespace

  # Kueue nominalQuota is DERIVED from the capacity caps that also set the Karpenter
  # NodePool spec.limits (platform_karpenter.tf) — one source of truth per tier, so
  # Kueue never admits more GPUs/CPU/memory than Karpenter is allowed to provision.
  # EFA quota is not a separate dial: a pod requesting EFA also requests a GPU and a
  # node carries ≤1 EFA, so per-flavor EFA demand ≤ that flavor's GPU quota — the
  # chart sets the g-flavor EFA nominalQuota equal to gpuGQuota.
  set = [
    { name = "clusterQueueName", value = var.kueue_cluster_queue_name },
    { name = "cohortName", value = "gpu-cohort" },
    { name = "gpuGQuota", value = tostring(var.gpu_g_capacity) },
    { name = "gpuQuota", value = tostring(var.gpu_p_capacity) },
    { name = "gpuLendingLimit", value = tostring(var.kueue_gpu_lending_limit) },
    { name = "cpuQuota", value = tostring(var.cpu_capacity) },
    { name = "memoryQuota", value = var.memory_capacity },
    { name = "workloadNamespace", value = "inference" },
    { name = "chartContentHash", value = local.chart_hashes["kueue"] },
  ]

  depends_on = [helm_release.kueue]
}
