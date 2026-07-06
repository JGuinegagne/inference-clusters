resource "aws_s3_bucket" "this" {
  bucket_prefix = var.bucket_name_prefix
  force_destroy = true

  tags = merge(var.combined_tags, {
    Name = var.bucket_name_prefix
  })
}

resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id

  versioning_configuration {
    status = "Enabled"
  }
}

# SSE-KMS with the AWS-managed aws/s3 key (no kms_master_key_id): its key policy lets
# same-account principals decrypt transparently through S3, so the node role (S3-direct
# streaming) and the Mountpoint-for-S3 CSI driver keep a pure s3:* read grant — no
# kms:Decrypt needed. Matches the jupyter-deploy base templates. bucket_key_enabled
# collapses per-object KMS calls to one data key per bucket/period.
resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket = aws_s3_bucket.this.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
