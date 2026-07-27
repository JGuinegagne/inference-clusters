# CI e2e image: extends the pytest-jupyter-deploy base (system packages, Terraform,
# AWS CLI, kubectl, helm) with this workspace's Python env, so the deploy and test
# jobs run against a self-contained image (no runtime workspace sync).
#
# `uv sync --all-packages` installs the published `jupyter-deploy` CLI (from PyPI, the
# same version users get) plus this repo's template package (editable, from the
# workspace) so `jd init -T karpenter` resolves its entry point.
#
# Layer strategy (dep-first COPY): manifests → uv sync (cached when deps unchanged)
# → full source. No uv.lock — resolve freely so CI matches a fresh `pip install`.

ARG BASE_IMAGE=inference-e2e-karpenter:base
FROM ${BASE_IMAGE}

# --- Dependency layer (cached when pyproject.toml unchanged) ---
# Copy manifests + stub the files hatchling validates (LICENSE, README.md) so a
# content edit does not bust this layer.
USER root
COPY --chown=testuser:testuser pyproject.toml /workspace/
COPY --chown=testuser:testuser libs/inference-tf-aws-eks-karpenter/pyproject.toml /workspace/libs/inference-tf-aws-eks-karpenter/
RUN touch /workspace/libs/inference-tf-aws-eks-karpenter/LICENSE \
        /workspace/libs/inference-tf-aws-eks-karpenter/README.md \
    && chown -R testuser /workspace/libs
USER testuser

WORKDIR /workspace
RUN rm -f uv.lock && uv sync --all-packages

# --- Source layer (changes every run, cheap) ---
USER root
COPY --chown=testuser:testuser . /workspace/
USER testuser

# Re-sync so the editable template package registers against the real source, then
# verify the CLI starts — `jd --help` eagerly loads every registered template entry
# point, so this fails the build if our template does not import.
RUN uv sync --all-packages \
    && uv run jupyter-deploy --help >/dev/null

CMD ["sleep", "infinity"]
