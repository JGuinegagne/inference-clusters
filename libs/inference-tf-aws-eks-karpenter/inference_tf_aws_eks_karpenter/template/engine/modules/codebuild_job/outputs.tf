output "project_name" {
  value = aws_codebuild_project.this.name
}

output "role_arn" {
  value = aws_iam_role.this.arn
  # depends_on the policy so consumers ordered on role_arn wait for permissions.
  depends_on = [aws_iam_role_policy.this]
}
