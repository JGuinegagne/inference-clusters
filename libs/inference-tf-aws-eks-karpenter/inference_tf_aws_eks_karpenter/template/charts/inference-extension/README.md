<!-- CRD manifests here are vendored, NOT hand-authored. -->

# Vendored CRDs — Gateway API Inference Extension

`crds.yaml` is copied verbatim from a pinned upstream release of the Gateway API
Inference Extension. It is **not** hand-written — CRD schemas are large and
version-specific, so we vendor the exact upstream file.

## Pinned version: v1.0.1

Chosen because it ships the **GA** group `inference.networking.k8s.io` that the
workload chart's router expects (`--pool-group=inference.networking.k8s.io`,
`apiVersion: inference.networking.k8s.io/v1`). Earlier releases (v0.5.x) only had the
alpha `inference.networking.x-k8s.io` group and would NOT match the chart's
InferencePool.

`crds.yaml` contains 3 CustomResourceDefinitions:
- `inferencepools.inference.networking.k8s.io` (GA)
- `inferencepools.inference.networking.x-k8s.io` (alpha, back-comat)
- `inferenceobjectives.inference.networking.x-k8s.io` (alpha sibling the EPP watches)

## Updating

Re-fetch from the release matching the EPP image the workload chart uses:
`https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/<tag>/manifests.yaml`
Keep the pin in sync with that EPP image tag.
