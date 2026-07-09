# All variables MUST be declared here without default values.
# Default values live in ./presets/defaults-all.tfvars.
#
# Each description follows the jd display convention: a single-line summary, then
# (optionally) a blank line, further explanation, and a Recommended/Example value.

variable "region" {
  description = <<-EOT
    The AWS region to deploy the cluster into.

    Refer to: https://docs.aws.amazon.com/global-infrastructure/latest/regions/aws-regions.html

    Example: us-west-2
  EOT
  type        = string
}

variable "cluster_name_prefix" {
  description = <<-EOT
    The prefix for the EKS cluster name.

    A random deployment suffix is appended so multiple deployments can coexist in
    the same AWS account and region.

    Recommended: inference
  EOT
  type        = string
}

variable "kubernetes_version" {
  description = <<-EOT
    The Kubernetes control-plane version for the EKS cluster.

    Refer to: https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html

    Recommended: 1.36
  EOT
  type        = string
}

variable "karpenter_version" {
  description = <<-EOT
    The version of the Karpenter Helm chart to install.

    Recommended: 1.13.0
  EOT
  type        = string
}

variable "custom_tags" {
  description = <<-EOT
    Additional tags applied to all resources created by this template.

    Recommended: {}
  EOT
  type        = map(string)
}

variable "cluster_log_retention_days" {
  description = <<-EOT
    Retention in days for the EKS control-plane CloudWatch log group.

    Recommended: 90
  EOT
  type        = number
}

variable "enable_nat_gateway" {
  description = <<-EOT
    The cluster egress posture.

    false (default) = endpoints-only: no NAT, no public subnets, nodes reach AWS
    only over VPC endpoints (enforces the artifacts-from-our-registry invariant).
    true = internet-egress enabled: adds an IGW + per-AZ NAT + public subnets
    for arbitrary public egress.

    Recommended: false
  EOT
  type        = bool
}

variable "bootstrap_instance_types" {
  description = <<-EOT
    The instance types for the system managed node group (control-loop pods only).

    The system NG is the sizing lever; Prometheus peak memory is the binding
    constraint, so the default is a 16 GB SKU rather than 8 GB.

    Recommended: ["m6i.xlarge"]
  EOT
  type        = list(string)
}

variable "bootstrap_desired_size" {
  description = <<-EOT
    The desired size of the system managed node group.

    Cluster Autoscaler moves this within min/max.

    Recommended: 2
  EOT
  type        = number
}

variable "bootstrap_min_size" {
  description = <<-EOT
    The minimum size of the system managed node group.

    Recommended: 2
  EOT
  type        = number
}

variable "bootstrap_max_size" {
  description = <<-EOT
    The maximum size of the system managed node group.

    Recommended: 6
  EOT
  type        = number
}

variable "admin_role_names" {
  description = <<-EOT
    IAM role names granted EKS cluster-admin via access entries.

    Granted in addition to the deploying caller.

    Recommended: []
  EOT
  type        = list(string)
}

variable "admin_user_names" {
  description = <<-EOT
    IAM user names granted EKS cluster-admin via access entries.

    Granted in addition to the deploying caller.

    Recommended: []
  EOT
  type        = list(string)
}

variable "metrics_server_chart_version" {
  description = <<-EOT
    The Helm chart version for metrics-server (HPA + kubectl top).

    Recommended: 3.12.2
  EOT
  type        = string
}

variable "nvidia_device_plugin_version" {
  description = <<-EOT
    The nvcr.io/nvidia/k8s-device-plugin image tag to vendor into ECR.

    Recommended: v0.17.1
  EOT
  type        = string
}

variable "nvidia_device_plugin_chart_version" {
  description = <<-EOT
    The Helm chart version for the NVIDIA device plugin.

    Recommended: 0.17.1
  EOT
  type        = string
}

variable "mountpoint_s3_csi_version" {
  description = <<-EOT
    The EKS addon version for the Mountpoint-for-S3 CSI driver (S3-mount StorageClass).

    Recommended: v2.7.0-eksbuild.1
  EOT
  type        = string
}

variable "nvidia_dcgm_exporter_version" {
  description = <<-EOT
    The nvcr.io/nvidia/k8s/dcgm-exporter image tag to vendor into ECR (GPU metrics).

    Recommended: 4.5.3-4.8.2-distroless
  EOT
  type        = string
}

variable "kube_prometheus_stack_chart_version" {
  description = <<-EOT
    The Helm chart version for kube-prometheus-stack (Prometheus + Grafana + Alertmanager).

    Recommended: 87.6.0
  EOT
  type        = string
}

variable "dcgm_exporter_chart_version" {
  description = <<-EOT
    The Helm chart version for the NVIDIA DCGM exporter.

    Recommended: 4.8.2
  EOT
  type        = string
}

variable "grafana_version" {
  description = <<-EOT
    The docker.io/grafana/grafana image tag to vendor into ECR.

    Grafana has no no-creds registry, so it is vendored. This MUST match the
    kube-prometheus-stack chart's Grafana appVersion.

    Recommended: 13.1.0
  EOT
  type        = string
}

variable "prometheus_retention" {
  description = <<-EOT
    The Prometheus metrics retention window.

    Recommended: 15d
  EOT
  type        = string
}

variable "prometheus_memory_limit" {
  description = <<-EOT
    The memory limit on the Prometheus pod.

    A cardinality spike OOM-kills it in isolation rather than taking down
    co-resident control-loop pods.

    Recommended: 6Gi
  EOT
  type        = string
}

variable "enable_container_insights" {
  description = <<-EOT
    Whether to install the CloudWatch Observability addon (Container Insights + Fluent Bit pod logs).

    Recommended: true
  EOT
  type        = bool
}

variable "keda_chart_version" {
  description = <<-EOT
    The Helm chart version for KEDA (pod autoscaling).

    The chart appVersion equals this, and it is also the image tag vendored into
    ECR (KEDA images are published only to ghcr.io, so all three are vendored).

    Recommended: 2.20.1
  EOT
  type        = string
}

variable "kro_chart_version" {
  description = <<-EOT
    The Helm chart and controller image version for KRO (resource orchestration).

    Both the chart and image come from registry.k8s.io/kro and are reached via ECR
    pull-through.

    Recommended: 0.9.2
  EOT
  type        = string
}

variable "enable_gpu_p_nodepool" {
  description = <<-EOT
    Whether to install the high-end GPU NodePool (p4d/p5/p5en — A100/H100/H200).

    A P node is expensive and quota-constrained; the pool CR costs nothing until a
    pod opts into it via the nvidia-p label + taint toleration.

    Recommended: true
  EOT
  type        = bool
}

variable "gpu_p_capacity_reservation_id" {
  description = <<-EOT
    An optional On-Demand Capacity Reservation (ODCR) id to pin the gpu-p pool to.

    P on-demand capacity is scarce, so orgs often reserve it. Empty = plain
    on-demand.

    Recommended: ""
  EOT
  type        = string
}

variable "common_images" {
  description = <<-EOT
    Common-utility image paths (busybox/certgen-class) made available to all nodes via ECR pull-through.

    Each entry is a full image path INCLUDING the registry host, and MUST resolve
    to a no-credentials trusted upstream (public.ecr.aws, quay.io, registry.k8s.io)
    — Docker Hub is not trusted, so use the ECR Public mirror (e.g.
    public.ecr.aws/docker/library/busybox). The deployer adds paths, not registries.

    Recommended: []
  EOT
  type        = list(string)

  validation {
    condition = alltrue([
      for img in var.common_images :
      can(regex("^(public\\.ecr\\.aws|quay\\.io|registry\\.k8s\\.io)/", img))
    ])
    error_message = "Every common_images entry must start with a trusted no-credentials registry host: public.ecr.aws/, quay.io/, or registry.k8s.io/ (Docker Hub/GHCR require credentials and are not supported; use the ECR Public mirror instead)."
  }
}

# === Multi-node inference (LWS + Kueue + EFA) ===

variable "enable_lws" {
  description = <<-EOT
    Install the LeaderWorkerSet controller for multi-node pod group lifecycle.

    Required for multi-node inference — manages leader/worker templates with
    RecreateGroupOnPodRestart semantics (NCCL groups are not recoverable).

    Recommended: true (for multi-node tracks)
  EOT
  type        = bool
}

variable "lws_chart_version" {
  description = <<-EOT
    The Helm chart version for LeaderWorkerSet.

    Published to registry.k8s.io (pull-through, no vendoring).

    Recommended: 0.6.2
  EOT
  type        = string
}

variable "enable_kueue" {
  description = <<-EOT
    Install Kueue for admission control and gang scheduling of LWS workloads.

    Kueue gates workloads behind GPU quota — the entire LWS group is admitted
    atomically or stays suspended. Includes TAS (AZ co-location for EFA),
    Prometheus ServiceMonitor, and waitForPodsReady (evicts on partial provisioning).

    Requires enable_lws = true (LWS CRD must exist for Kueue's integration).

    Recommended: true (for multi-node tracks)
  EOT
  type        = bool
}

variable "kueue_chart_version" {
  description = <<-EOT
    The Helm chart version for Kueue.

    Published to registry.k8s.io (pull-through, no vendoring).

    Recommended: 0.10.0
  EOT
  type        = string
}

variable "kueue_cluster_queue_name" {
  description = <<-EOT
    Name of the ClusterQueue for GPU inference workloads.

    Recommended: inference-gpu
  EOT
  type        = string
}

variable "kueue_cohort_name" {
  description = <<-EOT
    Cohort name for capacity borrowing/lending between queues.

    Recommended: gpu-cohort
  EOT
  type        = string
}

variable "kueue_gpu_quota" {
  description = <<-EOT
    Nominal GPU quota for the inference ClusterQueue.

    Recommended: 64
  EOT
  type        = number
}

variable "kueue_gpu_lending_limit" {
  description = <<-EOT
    Maximum GPUs lent to other queues in the cohort when idle.

    0 = no lending (all GPUs reserved for inference).

    Recommended: 0
  EOT
  type        = number
}

variable "kueue_cpu_quota" {
  description = <<-EOT
    Nominal CPU quota for the inference ClusterQueue.

    Recommended: 768
  EOT
  type        = number
}

variable "kueue_memory_quota" {
  description = <<-EOT
    Nominal memory quota for the inference ClusterQueue.

    Recommended: 4Ti
  EOT
  type        = string
}

variable "kueue_workload_namespace" {
  description = <<-EOT
    Namespace where the LocalQueue is created for inference workloads.

    Recommended: inference
  EOT
  type        = string
}

variable "kueue_wait_for_pods_ready_timeout" {
  description = <<-EOT
    How long Kueue waits for all pods to become Ready before evicting.

    This is the cost of the Kueue+Karpenter integration gap: if Karpenter
    can't provision all nodes, you burn this much idle GPU time per retry.

    Recommended: 15m
  EOT
  type        = string
}

variable "kueue_wait_for_pods_ready_retries" {
  description = <<-EOT
    Number of re-queue attempts on waitForPodsReady timeout.

    Recommended: 3
  EOT
  type        = number
}

variable "enable_efa" {
  description = <<-EOT
    Install the AWS EFA device plugin for multi-node NCCL networking.

    Required for multi-node inference with cross-node TP. Advertises EFA
    interfaces as allocatable resources on GPU nodes.

    Recommended: true (for multi-node tracks on p4d/p5/p5en)
  EOT
  type        = bool
}

variable "efa_device_plugin_chart_version" {
  description = <<-EOT
    The Helm chart version for the AWS EFA device plugin.

    Published to public.ecr.aws (pull-through).

    Recommended: 0.5.7
  EOT
  type        = string
}
