# === EFA device plugin — multi-node NCCL networking ===
#
# The EFA device plugin DaemonSet advertises EFA interfaces (vpc.amazonaws.com/efa)
# as allocatable resources on GPU nodes. Without it, pods can't request EFA
# and NCCL falls back to TCP (unusable for multi-node TP at scale).
#
# Placement: DaemonSet tolerates all taints so it runs on GPU dataplane nodes
# (where the EFA interfaces physically exist). Not on system NG.
#
# Images: the upstream chart defaults to a PRIVATE cross-account ECR image
# (602401143452.dkr.ecr.us-west-2.amazonaws.com/eks/aws-efa-k8s-device-plugin).
# This won't pull in an endpoints-only VPC. We repin to the public.ecr.aws mirror
# which resolves via our pull-through cache.
#
# Gated: only installed when enable_efa is true (not needed for single-node).

resource "helm_release" "efa_device_plugin" {
  count = var.enable_efa ? 1 : 0

  name       = "aws-efa-k8s-device-plugin"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-efa-k8s-device-plugin"
  version    = var.efa_device_plugin_chart_version
  namespace  = "kube-system"

  set = [
    # Repin image to pull-through URI (PRIMARY resolution). The upstream default
    # is a private cross-account ECR that can't be reached from an endpoints-only
    # VPC. public.ecr.aws/eks/aws-efa-k8s-device-plugin is the public mirror.
    {
      name  = "image.repository"
      value = "${local.ecr_registry}/ecr-public/eks/aws-efa-k8s-device-plugin"
    },
    { name = "image.tag", value = var.efa_device_plugin_chart_version },
    # DaemonSet must tolerate all taints so it lands on GPU nodes.
    { name = "tolerations[0].operator", value = "Exists" },
    # Only run on nodes that actually have EFA interfaces.
    { name = "nodeSelector.vpc\\.amazonaws\\.com/efa\\.present", value = "true" },
  ]

  depends_on = [
    null_resource.cluster_addons,
    null_resource.pullthrough_ready,
    module.node_group,
    helm_release.karpenter,
  ]
}
