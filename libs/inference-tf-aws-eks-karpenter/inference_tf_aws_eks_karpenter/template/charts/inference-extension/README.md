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

Run the re-vendor recipe from the repo root — it fetches the pinned upstream
release and regenerates `crds.yaml` (never hand-edit it):

```
just update-inference-extension-crds                 # uses the pinned version
just update-inference-extension-crds version=v1.1.0  # bump to a new release
```

The default pin lives in the `justfile` (`inference-extension-crd-version`). Keep
it in sync with the EPP image tag the workload chart (inference-charts) uses —
older releases (v0.5.x) only shipped the alpha group and won't match the chart's
`inference.networking.k8s.io/v1` InferencePool.
