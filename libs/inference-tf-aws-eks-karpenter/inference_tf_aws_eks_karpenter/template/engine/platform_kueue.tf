# === Kueue — admission control + gang scheduling for multi-node inference ===
#
# Kueue gates LWS workloads behind quota — the entire group is admitted
# atomically or stays suspended. Three features are always-on when enabled:
#
# 1. TopologyAwareScheduling (TAS): guarantees AZ co-location for EFA.
# 2. Prometheus ServiceMonitor: queue health visibility.
# 3. waitForPodsReady: evicts workload on partial provisioning failure.
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

    # TAS feature gate (list format: the chart renders --feature-gates= from this)
    { name = "controllerManager.featureGates[0].name", value = "TopologyAwareScheduling" },
    { name = "controllerManager.featureGates[0].enabled", value = "true" },

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
# First-party local chart: Topology, ResourceFlavor, ClusterQueue, LocalQueue.
# Installed as a helm_release (not kubernetes_manifest) because kubernetes_manifest
# requires a live cluster connection at plan time.
resource "helm_release" "kueue_config" {
  count     = var.enable_kueue ? 1 : 0
  name      = "kueue-config"
  chart     = "${path.module}/../charts/kueue"
  namespace = local.kueue_namespace

  set = [
    { name = "clusterQueueName", value = var.kueue_cluster_queue_name },
    { name = "cohortName", value = "gpu-cohort" },
    { name = "gpuQuota", value = tostring(var.kueue_gpu_quota) },
    { name = "gpuLendingLimit", value = tostring(var.kueue_gpu_lending_limit) },
    { name = "cpuQuota", value = tostring(var.kueue_cpu_quota) },
    { name = "memoryQuota", value = var.kueue_memory_quota },
    { name = "workloadNamespace", value = "inference" },
    { name = "chartContentHash", value = local.chart_hashes["kueue"] },
  ]

  depends_on = [helm_release.kueue]
}
