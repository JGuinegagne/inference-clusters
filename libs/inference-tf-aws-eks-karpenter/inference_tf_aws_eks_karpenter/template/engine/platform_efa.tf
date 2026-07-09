# === EFA device plugin — multi-node NCCL networking ===
#
# The EFA device plugin DaemonSet advertises EFA interfaces (vpc.amazonaws.com/efa)
# as allocatable resources on GPU nodes. Without it, pods can't request EFA
# and NCCL falls back to TCP (unusable for multi-node TP at scale).
#
# Placement: DaemonSet tolerates all taints so it runs on GPU dataplane nodes
# (where the EFA interfaces physically exist). Not on system NG.
#
# Images: published to public.ecr.aws (no-creds pull-through).
# Gated: only installed when enable_efa is true (not needed for single-node).

resource "helm_release" "efa_device_plugin" {
  count = var.enable_efa ? 1 : 0

  name       = "aws-efa-k8s-device-plugin"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-efa-k8s-device-plugin"
  version    = var.efa_device_plugin_chart_version
  namespace  = "kube-system"

  set = [
    # DaemonSet must tolerate all taints so it lands on GPU nodes.
    { name = "tolerations[0].operator", value = "Exists" },
    # Only run on nodes that actually have EFA interfaces.
    { name = "nodeSelector.vpc\\.amazonaws\\.com/efa\\.present", value = "true" },
  ]

  depends_on = [
    null_resource.cluster_addons,
    module.node_group,
    helm_release.karpenter,
  ]
}
