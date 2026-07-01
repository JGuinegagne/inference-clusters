<!-- CLAUDE.md is a symlink to this file -->

# Project Context

This is a monorepo of [jupyter-deploy](https://github.com/jupyter-infra/jupyter-deploy)
templates that provision **AWS EKS clusters for inference workloads**.

The `jupyter-deploy` CLI (`jd`) is **not** source-controlled here — it is consumed as a published
PyPI dependency (`jupyter-deploy[aws,k8s]`). This repo only ships **template packages**:
data payloads (Terraform, manifests, presets) plus a thin Python shim that registers each
template with the CLI through a `jupyter_deploy.terraform_templates` entry point.

Using `jd` to manage inference clusters is a deliberate POC shortcut: it buys us templated
Terraform, configuration presets, the `config`/`up`/`down` lifecycle, and a manifest-driven
command surface without building our own tooling.

## Workspace layout

uv workspace. Each publishable template is a member under `libs/`:

```
libs/<template-package>/
├── pyproject.toml                       # entry-point registration + build (hatchling)
├── <module>/
│   ├── __init__.py                      # __version__
│   ├── template.py                      # TEMPLATE_PATH = .../template
│   └── template/                        # payload scaffolded into a user project by `jd init`
│       ├── manifest.yaml                # template metadata, provider commands, progress phases
│       ├── variables.yaml               # variable definitions + config presets
│       ├── AGENT.md.template            # template-specific AI instructions (rendered on init)
│       └── engine/                      # terraform: main.tf, variables.tf, outputs.tf, modules/, presets/
└── tests/{unit,e2e}/
```

## Template packages

### EKS Karpenter base template
Code: `./libs/jumpstart-inference-tf-aws-eks-karpenter`

- infrastructure-as-code engine: `terraform`
- cloud provider: `aws`
- node autoscaling: [Karpenter](https://karpenter.sh) over **self-managed nodes**

A base EKS cluster intended as the foundation for inference workloads. Karpenter
provisions and scales self-managed nodes on demand (rather than relying solely on
EKS managed node groups).

#### Terraform conventions (inherited from jupyter-deploy)
- All variables MUST be defined in `engine/variables.tf` without default values.
  Default values MUST live in `engine/presets/defaults-all.tfvars`.
- There MUST NOT be any `variable` blocks in files other than `variables.tf`.
- All `local-exec` provisioners MUST set `interpreter = ["/bin/bash", "-c"]` —
  Terraform defaults to `/bin/sh`.
- `main.tf` MUST declare `template_name` and `template_version` locals; the version
  MUST stay in sync with `pyproject.toml`, `__init__.py`, and `manifest.yaml`
  (enforced by `tests/unit/test_*_version.py`).

## Development workflow

This repo is a uv workspace. Common commands (see `justfile`):

- `just sync` — `uv sync` the workspace
- `just lint` — `ruff format`, `ruff check --fix`, `mypy`, `terraform fmt`, `yamllint`
- `just unit-test` — `uv run pytest`

### General coding rules
1. You MUST NOT silence linters without the user's permission.
2. You MUST NOT write docstrings that merely repeat a method/function name.
3. You MUST NOT use `TYPE_CHECKING` imports anywhere.

### Writing unit tests
Unit tests live in `libs/<package>/tests/unit`.
1. Define a `unittest.TestCase` per class/function/major method under test.
2. Prefer `@patch()` / inline `with patch` over `pytest.fixtures`.
3. Always annotate patched args as `: Mock` for mypy.

### E2E tests
E2E tests live in `libs/<package>/tests/e2e` and use the `pytest-jupyter-deploy`
plugin (the `e2e_deployment` fixture, `undeployed_project` helper, etc.). They run
inside a container whose image is **vended by `pytest-jupyter-deploy`** — there is no
Dockerfile in this repo.

The base compose file (vended by the plugin) hardcodes the container/image name to
`jupyter-deploy-e2e`. We merge a committed override (`docker-compose.e2e-name.yml`) on
every compose call so this repo's container/image are named `jumpstart-inference-e2e` —
they never collide with a jupyter-deploy E2E run on the same host.

- `just e2e-up` — build + start the E2E container.
- `just test-e2e-eks-karpenter sandbox-e2e test_configuration` — run config-only tests
  (just `jd init`/`jd config`, **no AWS required**).
- `just test-e2e-eks-karpenter sandbox-e2e "" full-deploy=true,destroy=true` — provision a
  real cluster, run `full_deployment`-marked tests, then tear it down (**requires AWS**).
- `just e2e-down` / `just clean-e2e` — stop / clean up.

Test layers:
- `test_configuration.py` — scaffolding + `jd config` validation, no cloud resources.
- `test_deployment.py` — `@pytest.mark.full_deployment` smoke tests against a live cluster.

Copy `env.example` to `.env` at the repo root before running (the recipes populate
`HOST_UID`/`HOST_GID`/`AWS_REGION` automatically).
