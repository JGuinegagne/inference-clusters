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
# Images: the chart's DEFAULT image is the EKS-managed regional ECR
# (602401143452.dkr.ecr.us-west-2.amazonaws.com/eks/aws-efa-k8s-device-plugin) —
# the SAME account the cluster already pulls vpc-cni/kube-proxy/coredns from. The
# node role's AmazonEC2ContainerRegistryReadOnly grants cross-account pull, and
# the endpoints-only VPC reaches it (ECR interface endpoint + S3 gateway for
# layers). So we deliberately DO NOT override image.repository — the default just
# works, no pull-through and no vendoring needed. (The image is NOT on
# public.ecr.aws, verified — so pull-through can't proxy it anyway.)
#
# NOTE: the EKS ECR account is region-specific. 602401143452 is us-west-2's; the
# chart default hardcodes us-west-2, so a cluster in another region must override
# image.repository to that region's EKS account. Out of scope for this POC
# (us-west-2 default).
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
    # DaemonSet must tolerate all taints so it lands on GPU nodes. Image is the
    # chart default (EKS regional ECR) — nodes already have access (see header).
    # The chart's built-in nodeAffinity on supportedInstanceLabels limits it to
    # EFA-capable instance types — no extra nodeSelector needed.
    { name = "tolerations[0].operator", value = "Exists" },
  ]

  depends_on = [
    null_resource.cluster_addons,
    module.node_group,
    helm_release.karpenter,
  ]
}
