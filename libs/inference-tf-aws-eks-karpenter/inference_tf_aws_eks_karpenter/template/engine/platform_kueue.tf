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

  # LWS integration (requires LWS CRD — enable_lws must also be true)
  set = [
    { name = "enableLeaderWorkerSet", value = "true" },

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

  # waitForPodsReady must be set via controllerManagerConfigYaml (not --set scalars).
  values = [yamlencode({
    controllerManager = {
      controllerManagerConfigYaml = yamlencode({
        apiVersion = "config.kueue.x-k8s.io/v1beta2"
        kind       = "Configuration"
        health = {
          healthProbeBindAddress = ":8081"
        }
        metrics = {
          bindAddress = ":8443"
        }
        waitForPodsReady = {
          enable  = true
          timeout = "15m"
          requeuingStrategy = {
            timestamp          = "Creation"
            backoffLimitCount  = 3
          }
        }
      })
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
