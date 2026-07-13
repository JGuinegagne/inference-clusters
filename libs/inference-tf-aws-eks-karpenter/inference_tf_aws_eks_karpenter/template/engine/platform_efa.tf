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
# Images: the upstream chart defaults to a PRIVATE cross-account ECR image
# (602401143452.dkr.ecr.us-west-2.amazonaws.com/eks/aws-efa-k8s-device-plugin).
# This won't pull in an endpoints-only VPC. We repin to the public.ecr.aws mirror
# which resolves via our pull-through cache. NOTE: chart version (v0.5.29) and
# image version (v0.5.20 = appVersion) diverge — we let the chart default the tag
# from appVersion rather than pinning explicitly.
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
    # Repin image repository to pull-through URI. Let the chart default tag from
    # appVersion (v0.5.20) — chart version (v0.5.29) != image version.
    {
      name  = "image.repository"
      value = "${local.ecr_registry}/ecr-public/eks/aws-efa-k8s-device-plugin"
    },
    # DaemonSet must tolerate all taints so it lands on GPU nodes.
    # The chart's built-in nodeAffinity on supportedInstanceLabels already limits
    # it to EFA-capable instance types (p4d, p5, trn1, etc.) — no extra
    # nodeSelector needed.
    { name = "tolerations[0].operator", value = "Exists" },
  ]

  depends_on = [
    null_resource.cluster_addons,
    null_resource.pullthrough_ready,
    module.node_group,
    helm_release.karpenter,
  ]
}
