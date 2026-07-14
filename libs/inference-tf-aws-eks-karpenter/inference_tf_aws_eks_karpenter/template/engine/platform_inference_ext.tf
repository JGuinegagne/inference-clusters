# === Gateway API Inference Extension — InferencePool CRDs (CRD-only) ===
#
# The Gateway API Inference Extension defines the custom resource types a KV-aware
# inference router uses:
#   - inferencepools.inference.networking.k8s.io  (GA group) — the "address book":
#     the set of model-server Pods a router (Endpoint Picker / EPP) may route between.
#   - inference.networking.x-k8s.io alpha siblings (inferenceobjectives, etc.) — the
#     EPP watches these whether or not the customer creates any; absent, it logs
#     "Failed to watch" and never marks the pool initialized.
#
# CRD-ONLY here. A CRD is a cluster-scoped API registration — one per cluster — so it
# belongs to the platform, not to a per-workload chart (two charts installing the same
# cluster-scoped CRD would collide). The EPP + Envoy data plane themselves are
# namespaced and ship in the workload chart (inference-charts), not here.
#
# Opt-in: gated on var.enable_inference_routing (off by default).
#
# Install: the upstream ships a single CRD manifest per release. We vendor it into the
# template (charts/inference-extension) and apply it as a CRD-only helm release so its
# lifecycle (and destroy ordering) is managed like the other first-party charts. No
# controller image is pulled — these are just CustomResourceDefinition objects.

locals {
  # Namespace holding only the Helm release metadata for the CRD install (the CRDs
  # themselves are cluster-scoped). Self-contained here so this arm has no dependency
  # on any other platform component's namespace.
  inference_ext_namespace = "inference-extension"
}

resource "helm_release" "inference_extension_crds" {
  count = var.enable_inference_routing ? 1 : 0

  name             = "inference-extension-crds"
  chart            = "${path.module}/../charts/inference-extension"
  namespace        = local.inference_ext_namespace
  create_namespace = true

  set = [
    # Chart content hash so editing a bundled CRD triggers a re-apply (see main.tf).
    { name = "chartContentHash", value = local.chart_hashes["inference-extension"] },
  ]

  depends_on = [
    null_resource.cluster_addons,
    module.node_group,
  ]
}
