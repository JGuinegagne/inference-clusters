variable "project_name" {
  type = string
}

variable "buildspec" {
  type        = string
  description = "Inline buildspec (YAML) the project runs. Source is NO_SOURCE — the job is env-driven."
}

variable "ecr_repository_arns" {
  type        = list(string)
  description = "ECR repository ARNs the job is allowed to push to (mirror/vendor targets)."
}

variable "environment_variables" {
  type        = map(string)
  description = "Static environment variables set on the project. Per-run values are passed via start-build overrides."
}

variable "combined_tags" {
  type = map(string)
}

variable "compute_type" {
  type        = string
  description = "CodeBuild compute type. Default SMALL fits image mirroring; chart-onboard weight ingest (10s-100s of GB) needs a larger type."
  default     = "BUILD_GENERAL1_SMALL"
}

variable "extra_policy_json" {
  type        = string
  description = "Optional additional IAM policy (JSON) attached to the job role — e.g. S3 read/write for the chart-onboard weights ingest. Empty = none."
  default     = ""
}
