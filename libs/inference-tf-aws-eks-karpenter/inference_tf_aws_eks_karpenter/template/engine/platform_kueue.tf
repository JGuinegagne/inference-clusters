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
  repository       = "https://kubernetes-sigs.github.io/kueue"
  chart            = "kueue"
  version          = var.kueue_chart_version
  namespace        = local.kueue_namespace
  create_namespace = true

  set = [
    # LWS integration (requires LWS CRD — enable_lws must also be true)
    { name = "enableLeaderWorkerSet", value = "true" },

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
    { name = "controller.nodeSelector.inference/role", value = "system" },
    { name = "controller.tolerations[0].key", value = "inference/role" },
    { name = "controller.tolerations[0].operator", value = "Equal" },
    { name = "controller.tolerations[0].value", value = "system" },
    { name = "controller.tolerations[0].effect", value = "NoSchedule" },
  ]

  depends_on = [
    null_resource.cluster_addons,
    null_resource.pullthrough_ready,
    module.node_group,
    helm_release.kube_prometheus_stack,
    helm_release.leader_worker_set,
  ]
}

# --- Topology resource for TAS ---
# Defines the data center hierarchy so Kueue can enforce AZ co-location.
resource "kubernetes_manifest" "kueue_topology" {
  count = var.enable_kueue ? 1 : 0

  manifest = {
    apiVersion = "kueue.x-k8s.io/v1alpha1"
    kind       = "Topology"
    metadata = {
      name = "default"
    }
    spec = {
      levels = [
        { nodeLabel = "topology.kubernetes.io/zone" },
        { nodeLabel = "kubernetes.io/hostname" },
      ]
    }
  }

  depends_on = [helm_release.kueue]
}

# --- ResourceFlavor for GPU nodes (references topology for TAS) ---
resource "kubernetes_manifest" "kueue_gpu_flavor" {
  count = var.enable_kueue ? 1 : 0

  manifest = {
    apiVersion = "kueue.x-k8s.io/v1beta1"
    kind       = "ResourceFlavor"
    metadata = {
      name = "gpu-multinode"
    }
    spec = {
      nodeLabels = {
        "karpenter.sh/nodepool" = "nvidia-p"
      }
      topologyName = "default"
      tolerations = [{
        key      = "nvidia.com/gpu"
        operator = "Exists"
        effect   = "NoSchedule"
      }]
    }
  }

  depends_on = [kubernetes_manifest.kueue_topology]
}

# --- ClusterQueue with cohort lending ---
resource "kubernetes_manifest" "kueue_cluster_queue" {
  count = var.enable_kueue ? 1 : 0

  manifest = {
    apiVersion = "kueue.x-k8s.io/v1beta1"
    kind       = "ClusterQueue"
    metadata = {
      name = var.kueue_cluster_queue_name
    }
    spec = {
      cohort = var.kueue_cohort_name
      resourceGroups = [{
        coveredResources = ["cpu", "memory", "nvidia.com/gpu"]
        flavors = [{
          name = "gpu-multinode"
          resources = [
            { name = "nvidia.com/gpu", nominalQuota = var.kueue_gpu_quota, lendingLimit = var.kueue_gpu_lending_limit },
            { name = "cpu", nominalQuota = var.kueue_cpu_quota },
            { name = "memory", nominalQuota = var.kueue_memory_quota },
          ]
        }]
      }]
      preemption = {
        withinClusterQueue  = "LowerPriority"
        reclaimWithinCohort = "LowerPriority"
      }
    }
  }

  depends_on = [kubernetes_manifest.kueue_gpu_flavor]
}

# --- LocalQueue in the workload namespace ---
resource "kubernetes_manifest" "kueue_local_queue" {
  count = var.enable_kueue ? 1 : 0

  manifest = {
    apiVersion = "kueue.x-k8s.io/v1beta1"
    kind       = "LocalQueue"
    metadata = {
      name      = "inference"
      namespace = var.kueue_workload_namespace
    }
    spec = {
      clusterQueue = var.kueue_cluster_queue_name
    }
  }

  depends_on = [kubernetes_manifest.kueue_cluster_queue]
}
