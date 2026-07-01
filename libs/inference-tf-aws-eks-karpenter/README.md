# JumpStart Inference — AWS EKS Karpenter base template

A [jupyter-deploy](https://github.com/jupyter-infra/jupyter-deploy) Terraform template that
provisions a **base AWS EKS cluster** with [Karpenter](https://karpenter.sh) for node
autoscaling over **self-managed nodes**. It is intended as the foundation that inference
workloads are layered onto.

- infrastructure-as-code engine: `terraform`
- cloud provider: `aws`
- node autoscaling: Karpenter (self-managed nodes)

> **Status:** seed scaffold. The Terraform engine under
> `inference_tf_aws_eks_karpenter/template/engine/` is a skeleton — the cluster,
> Karpenter install, and node pools are added in follow-up commits.

## Usage

This template is meant to be used with the
[jupyter-deploy](https://github.com/jupyter-infra/jupyter-deploy) CLI.

```bash
# install the CLI with AWS + Kubernetes support, plus this template
uv add "jupyter-deploy[aws,k8s]" inference-tf-aws-eks-karpenter

# scaffold a project from this template
jd init my-inference-cluster
# choose the "aws_eks_karpenter" template when prompted
```

## License

This project is licensed under the [MIT License](LICENSE).
