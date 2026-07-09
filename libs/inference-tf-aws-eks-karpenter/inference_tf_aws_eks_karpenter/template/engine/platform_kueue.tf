# === Kueue — admission control + gang scheduling for multi-node inference ===
#
# Kueue gates LWS workloads behind quota — the entire group is admitted
# atomically or stays suspended. Three features are always-on when enabled:
#
# 1. TopologyAwareScheduling (TAS): guarantees AZ co-location for EFA.
#    Workload pods annotated with podset-required-topology land in the same
#    AZ *before* admission. Without TAS, Karpenter might provision nodes
#    across AZs and NCCL hangs because EFA can't cross AZ boundaries.
#
# 2. Prometheus ServiceMonitor: queue health visibility (admission latency,
#    eviction rate, quota utilization). Required for production observability.
#
# 3. waitForPodsReady: if Karpenter can't provision all nodes (3/4 arrive),
#    Kueue evicts the workload after timeout, freeing GPUs for other work.
#    Without this, partial provisioning silently leaks expensive GPUs.
#
# Integration with Karpenter: indirect but functional. Kueue un-gates pods →
# pods go Pending → Karpenter reacts. No native ProvisioningRequest API
# (Karpenter doesn't implement it). The gap is covered by waitForPodsReady.
#
# Placement: controller on the tainted system NG.
#
# Images/chart: published to registry.k8s.io (no-creds pull-through).

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
    # LWS integration (requires LWS CRD — enable_lws must also be true)
    { name = "enableLeaderWorkerSet", value = "true" },

    # Repin the controller image to its pull-through URI (PRIMARY resolution):
    # registry.k8s.io/kueue/kueue -> <registry>/registry-k8s/kueue/kueue.
    { name = "controllerManager.manager.image.repository", value = "${local.ecr_registry}/registry-k8s/kueue/kueue" },
    { name = "controllerManager.manager.image.tag", value = "v${var.kueue_chart_version}" },

    # TopologyAwareScheduling — required for EFA co-location
    { name = "controller.featureGates.TopologyAwareScheduling", value = "true" },

    # Prometheus ServiceMonitor — required for queue health visibility
    { name = "controller.metrics.serviceMonitor.enabled", value = "true" },

    # waitForPodsReady — prevents silent GPU leaks on partial provisioning
    { name = "controller.waitForPodsReady.enable", value = "true" },
    { name = "controller.waitForPodsReady.timeout", value = var.kueue_wait_for_pods_ready_timeout },
    { name = "controller.waitForPodsReady.requeuingStrategy.timestamp", value = "Creation" },
    { name = "controller.waitForPodsReady.requeuingStrategy.backoffLimitCount", value = tostring(var.kueue_wait_for_pods_ready_retries) },

    # System NG placement.
    { name = "controllerManager.manager.nodeSelector.inference/role", value = "system" },
    { name = "controllerManager.manager.tolerations[0].key", value = "inference/role" },
    { name = "controllerManager.manager.tolerations[0].operator", value = "Equal" },
    { name = "controllerManager.manager.tolerations[0].value", value = "system" },
    { name = "controllerManager.manager.tolerations[0].effect", value = "NoSchedule" },
  ]

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
# requires a live cluster connection at plan time — which doesn't exist during
# `jd config` / `terraform plan` on a fresh scaffold. The local chart pattern
# (same as charts/kro, charts/karpenter) defers CRD validation to apply time.
resource "helm_release" "kueue_config" {
  count     = var.enable_kueue ? 1 : 0
  name      = "kueue-config"
  chart     = "${path.module}/../charts/kueue"
  namespace = local.kueue_namespace

  set = [
    { name = "clusterQueueName", value = var.kueue_cluster_queue_name },
    { name = "cohortName", value = var.kueue_cohort_name },
    { name = "gpuQuota", value = tostring(var.kueue_gpu_quota) },
    { name = "gpuLendingLimit", value = tostring(var.kueue_gpu_lending_limit) },
    { name = "cpuQuota", value = tostring(var.kueue_cpu_quota) },
    { name = "memoryQuota", value = var.kueue_memory_quota },
    { name = "workloadNamespace", value = var.kueue_workload_namespace },
    { name = "chartContentHash", value = local.chart_hashes["kueue"] },
  ]

  depends_on = [helm_release.kueue]
}
