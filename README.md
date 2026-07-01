# JumpStart Inference Clusters

Monorepo of [jupyter-deploy](https://github.com/jupyter-infra/jupyter-deploy) templates that
provision **EKS clusters for inference workloads**.

These templates reuse the `jupyter-deploy` (`jd`) CLI to scaffold, configure, and deploy
the cluster infrastructure with a few simple commands. Using `jd` to manage inference clusters
is a deliberate shortcut for a proof-of-concept: it gives us templated Terraform, presets,
config/up/down lifecycle, and a manifest-driven command surface for free.

## Repository layout

This repository is a [uv](https://github.com/astral-sh/uv) workspace. The `jupyter-deploy` CLI core
is consumed as a published PyPI dependency; each template package under `libs/` is a workspace member
that registers itself with the CLI via a `jupyter_deploy.terraform_templates` entry point.

## Packages

- [jumpstart-inference-tf-aws-eks-karpenter](./libs/jumpstart-inference-tf-aws-eks-karpenter/README.md):
  A Terraform template that provisions a base AWS EKS cluster with [Karpenter](https://karpenter.sh)
  for node autoscaling over **self-managed nodes**, intended as the foundation for inference workloads.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [just](https://github.com/casey/just#installation) (e.g. `brew install just`, `cargo install just`,
  or see the install guide for your platform)

## Getting started

```bash
# create the virtual environment and install the workspace
uv sync

# the jd CLI is available with the inference templates registered
uv run jd --help
```

## Development

```bash
# lint your changes
just lint

# run the unit tests
just unit-test
```

See [AGENT.md](./AGENT.md) for repository conventions.

## License

This project is licensed under the [MIT License](LICENSE).
