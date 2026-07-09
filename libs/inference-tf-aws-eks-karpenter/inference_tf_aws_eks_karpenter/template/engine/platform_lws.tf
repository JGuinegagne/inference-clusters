# === LeaderWorkerSet — multi-node pod group lifecycle ===
#
# LWS manages leader+worker pod groups with coordinated lifecycle
# (RecreateGroupOnPodRestart). Required for multi-node inference where NCCL
# process groups are not recoverable — if one pod dies, all must restart.
#
# Placement: the single controller pod on the tainted system NG — it watches
# LWS CRs and manages pod templates; the actual inference pods it manages
# land on Karpenter GPU nodes.
#
# Images/chart: published to registry.k8s.io (no-creds pull-through).

locals {
  lws_namespace = "lws-system"
}

resource "helm_release" "leader_worker_set" {
  count = var.enable_lws ? 1 : 0

  name             = "leader-worker-set"
  repository       = "https://kubernetes-sigs.github.io/leader-worker-set"
  chart            = "leader-worker-set"
  version          = var.lws_chart_version
  namespace        = local.lws_namespace
  create_namespace = true

  set = [
    { name = "replicaCount", value = "1" },
    # System NG placement.
    { name = "nodeSelector.inference/role", value = "system" },
    { name = "tolerations[0].key", value = "inference/role" },
    { name = "tolerations[0].operator", value = "Equal" },
    { name = "tolerations[0].value", value = "system" },
    { name = "tolerations[0].effect", value = "NoSchedule" },
  ]

  depends_on = [
    null_resource.cluster_addons,
    null_resource.pullthrough_ready,
    module.node_group,
  ]
}
