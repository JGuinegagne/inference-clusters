resource "time_sleep" "wait_for_nodes" {
  create_duration = "30s"
  depends_on      = [module.node_group]
}

locals {
  # The system NG is tainted inference/role=system:NoSchedule. Every
  # Deployment-based addon we place there must tolerate it. vpc-cni and kube-proxy
  # are tolerate-all DaemonSets and need no toleration.
  system_toleration = {
    key      = "inference/role"
    operator = "Equal"
    value    = "system"
    effect   = "NoSchedule"
  }
}

# --- Addon ordering aggregators (eks-oidc pattern) ---

# DaemonSet addons a node needs to be functional: vpc-cni (pod IPs) and kube-proxy
# (Service/ClusterIP routing). The node group depends_on this, so on create the
# CNI is in place before nodes join, and on destroy the nodes drain BEFORE these
# are removed. Only DaemonSets belong here (they report healthy with zero nodes,
# so requiring them before the node group never deadlocks).
resource "null_resource" "core_node_addons" {
  depends_on = [
    aws_eks_addon.vpc_cni,
    aws_eks_addon.kube_proxy,
  ]
}

# Every cluster addon. Helm releases depend_on this, so on create all
# addons are up before any chart installs, and on destroy every chart uninstalls
# BEFORE any addon is removed (ebs-csi for PVC/PV teardown, coredns for in-cluster
# DNS, ...). Add a new addon here once and all charts inherit the ordering.
#
# The admin access-policy associations are also pulled in here: they are what
# authorize the Helm/Kubernetes providers to reach the cluster. On destroy (reverse
# order) every chart routes through this aggregator, so all charts uninstall BEFORE
# the association is torn down — otherwise the providers lose authorization mid-destroy
# and remaining uninstalls fail "forbidden". The node access entry is kept alive
# for the same reason: nodes must stay joined until charts are gone.
resource "null_resource" "cluster_addons" {
  depends_on = [
    aws_eks_addon.vpc_cni,
    aws_eks_addon.kube_proxy,
    aws_eks_addon.coredns,
    aws_eks_addon.pod_identity_agent,
    aws_eks_addon.ebs_csi_driver,
    aws_eks_addon.s3_csi_driver,
    aws_eks_access_policy_association.admin_role,
    aws_eks_access_policy_association.admin_user,
    aws_eks_access_entry.node,
  ]
}

resource "aws_eks_addon" "vpc_cni" {
  cluster_name = module.eks_cluster.cluster_name
  addon_name   = "vpc-cni"
  tags         = local.combined_tags
}

resource "aws_eks_addon" "kube_proxy" {
  cluster_name = module.eks_cluster.cluster_name
  addon_name   = "kube-proxy"
  tags         = local.combined_tags
}

resource "aws_eks_addon" "coredns" {
  cluster_name = module.eks_cluster.cluster_name
  addon_name   = "coredns"
  tags         = local.combined_tags

  # coredns is a Deployment — it must tolerate the tainted system NG to schedule.
  configuration_values = jsonencode({
    tolerations = [local.system_toleration]
  })

  depends_on = [time_sleep.wait_for_nodes]
}

resource "aws_eks_addon" "pod_identity_agent" {
  cluster_name = module.eks_cluster.cluster_name
  addon_name   = "eks-pod-identity-agent"
  tags         = local.combined_tags
}

resource "aws_eks_addon" "ebs_csi_driver" {
  cluster_name = module.eks_cluster.cluster_name
  addon_name   = "aws-ebs-csi-driver"
  tags         = local.combined_tags

  # The controller is a Deployment (tolerate the system taint); the node plugin is
  # a DaemonSet that must tolerate all so it also runs on Karpenter nodes.
  configuration_values = jsonencode({
    controller = {
      tolerations = [local.system_toleration]
    }
    node = {
      tolerateAllTaints = true
    }
  })

  pod_identity_association {
    role_arn        = module.ebs_csi_role.role_arn
    service_account = "ebs-csi-controller-sa"
  }

  depends_on = [aws_eks_addon.pod_identity_agent]
}

# Mountpoint-for-S3 CSI driver — mounts s3://<bucket>/models as a read-only POSIX
# path (the s3-models StorageClass, charts/storage). Authenticates via Pod Identity
# to the dedicated s3_csi_role (platform_storage.tf), NOT the node role. The
# node plugin is a DaemonSet that must tolerate all taints so mounts work on GPU /
# Karpenter nodes; the controller is a Deployment pinned to the tainted system NG.
resource "aws_eks_addon" "s3_csi_driver" {
  cluster_name  = module.eks_cluster.cluster_name
  addon_name    = "aws-mountpoint-s3-csi-driver"
  addon_version = var.mountpoint_s3_csi_version
  tags          = local.combined_tags

  configuration_values = jsonencode({
    controller = {
      tolerations = [local.system_toleration]
    }
    node = {
      tolerateAllTaints = true
    }
  })

  pod_identity_association {
    role_arn        = module.s3_csi_role.role_arn
    service_account = "s3-csi-driver-sa"
  }

  depends_on = [aws_eks_addon.pod_identity_agent]
}
