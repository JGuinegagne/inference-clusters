# `depends_on` on the output makes every CONSUMER of role_arn transitively depend
# on the policy attachments, not just on the bare role. This is what guarantees:
#   - on create: the policies are attached before the consumer (e.g. node group)
#     comes up, so nodes never boot with missing worker/CNI permissions;
#   - on destroy: the consumer is torn down before the attachments are removed, so
#     a node group's CNI/worker policies stay attached until the nodes are gone.
# Without it, role_arn references only aws_iam_role.this.arn (the role), leaving
# the attachments unordered against the consumer — see the note in main.tf.
output "role_arn" {
  value      = aws_iam_role.this.arn
  depends_on = [aws_iam_role_policy_attachment.policies]
}

output "role_name" {
  value = aws_iam_role.this.name
}
