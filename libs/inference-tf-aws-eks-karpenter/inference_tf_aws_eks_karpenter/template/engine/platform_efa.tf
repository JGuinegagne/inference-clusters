# === EFA device plugin — multi-node NCCL networking ===
#
# The EFA device plugin DaemonSet advertises EFA interfaces (vpc.amazonaws.com/efa)
# as allocatable resources on GPU nodes. Without it, pods can't request EFA
# and NCCL falls back to TCP (unusable for multi-node TP at scale).
#
# Placement: DaemonSet tolerates all taints so it runs on GPU dataplane nodes
# (where the EFA interfaces physically exist). Not on system NG. The chart has
# a built-in requiredDuringScheduling nodeAffinity on supportedInstanceLabels
# that gates it to EFA-capable instance types — do NOT remove that affinity or
# this hostNetwork/system-node-critical DaemonSet would spray onto every node.
#
# Images: the EFA plugin image lives ONLY on the EKS-managed regional ECR (it is
# NOT on public.ecr.aws, so pull-through can't proxy it). The chart default
# hardcodes us-west-2's account (602401143452), which breaks in other regions.
# Instead we VENDOR it into our own ECR (images.tf) from the regional registry
# INFERRED from the vpc-cni add-on — never a hardcoded account — and repin the
# release to that private copy, so nodes pull it like any other platform image
# over the endpoints-only VPC. Works in every commercial region automatically.
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
    # Repin to the vendored private-ECR copy (see images.tf) — the source registry
    # is inferred from vpc-cni, not hardcoded. The chart otherwise defaults to
    # us-west-2's EKS account, which fails in other regions.
    {
      name  = "image.repository"
      value = aws_ecr_repository.vendored["efa_device_plugin"].repository_url
    },
    { name = "image.tag", value = local.vendored_tag },
    # DaemonSet must tolerate all taints so it lands on GPU nodes (where the EFA
    # interfaces physically exist). The chart's built-in nodeAffinity on
    # supportedInstanceLabels limits it to EFA-capable types — no nodeSelector.
    { name = "tolerations[0].operator", value = "Exists" },
  ]

  depends_on = [
    null_resource.cluster_addons,
    null_resource.image_vendor,
    module.node_group,
    helm_release.karpenter,
  ]
}
