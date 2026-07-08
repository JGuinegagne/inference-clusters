"""Gated live E2E — the onboarder's large-weights server-side copy at scale.

Onboards a chart whose weights: source is NVIDIA Nemotron-3-Super-120B-A12B (BF16) from
the SageMaker JumpStart public cache: ~230 GiB across 75 objects (tiny configs + multi-GB
safetensors shards). Proves the server-side S3 multipart copy (UploadPartCopy — S3 moves
the bytes internally) handles a model that FAR exceeds the CodeBuild EBS (128GB): no local
disk, no NIC transit. Asserts every source object lands in our S3 at the same total size.

It does NOT serve the model (a 120B needs P-class GPUs) — the copy is the thing under test.

Marked `full_deployment` and slow. Server-side copy is fast (~3x a read-then-write stream,
which bypasses the CodeBuild NIC ceiling), but 230 GiB + the CodeBuild cold start still runs
past the default onboarder build ceiling, so it raises max_polls. Runs only against a
deployed cluster with full-deploy=true.
"""

import pytest
from pytest_jupyter_deploy.deployment import EndToEndDeployment

from tests.e2e import _serving_helpers as h

CHART = "weights-import"
# The chart's weights.model.source key (under the JumpStart bucket) and its .name — the
# models/<name> subdir the onboarder copies into. Kept in sync with the chart values.yaml.
# The bucket itself embeds the region, so it comes from JUMPSTART_PUBLIC_BUCKET_NAME (via
# h.jumpstart_bucket), NOT a hardcoded name; the chart fixture uses the same placeholder.
SOURCE_KEY = "huggingface-llm/huggingface-llm-nvidia-nemotron-3-super-120b-a12b-bf16/artifacts/inference-prepack/v1.0.0"
WEIGHT_NAME = "nemotron-3-super-120b"
# ~90 min ceiling (270 x 20s): the CodeBuild cold start + a 230 GiB copy is the long pole.
MAX_POLLS = 270


@pytest.mark.full_deployment
def test_large_weights_import_streams_to_s3(e2e_deployment: EndToEndDeployment) -> None:
    e2e_deployment.ensure_deployed()
    region = h.jd_output(e2e_deployment, "region")

    # Source truth: how many objects / bytes the JumpStart prefix holds.
    source_uri = f"s3://{h.jumpstart_bucket()}/{SOURCE_KEY}"
    src_count, src_bytes = h.s3_prefix_stats(source_uri)
    assert src_count > 1 and src_bytes > 100 * 1024**3, (
        f"expected a large multi-object source, got {src_count} objects / {src_bytes} bytes"
    )

    dst_uri = f"{h.jd_output(e2e_deployment, 'models_s3_uri')}/{WEIGHT_NAME}"
    try:
        # Onboard: copies the weights prefix into s3://<bucket>/models/<name> (no local disk).
        overrides = h.onboard_chart(e2e_deployment, region, CHART, max_polls=MAX_POLLS)
        assert dst_uri in overrides.read_text(), f"overrides must repoint weights at {dst_uri}"

        # Every source object landed, at the same total size (server-side copy is byte-exact).
        dst_count, dst_bytes = h.s3_prefix_stats(dst_uri)
        assert dst_count == src_count, f"object count mismatch: source {src_count}, dest {dst_count}"
        assert dst_bytes == src_bytes, f"byte-size mismatch: source {src_bytes}, dest {dst_bytes}"
    finally:
        # Always purge the ~230 GiB copy — pass or fail — so it never lingers in the store.
        h.delete_s3_prefix(dst_uri)
