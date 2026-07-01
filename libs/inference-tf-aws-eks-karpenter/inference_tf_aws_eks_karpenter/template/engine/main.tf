terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = ">= 2.14"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.30"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.0"
    }
  }
}

provider "aws" {
  region = var.region
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

resource "random_id" "postfix" {
  byte_length = 4
}

locals {
  template_name    = "tf-aws-eks-karpenter"
  template_version = "0.1.0rc1"

  default_tags = merge(
    {
      Source       = "inference"
      Template     = local.template_name
      Version      = local.template_version
      DeploymentId = random_id.postfix.hex
    },
    var.custom_tags,
  )

  doc_postfix = random_id.postfix.hex
}

# NOTE: seed scaffold. VPC, EKS cluster, Karpenter install, and self-managed
# NodePools/EC2NodeClasses are added in follow-up commits.
